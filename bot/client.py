"""
Discord bot client: prefix commands and transcript delivery.

Deviations from upstream, both deliberate:
  * !transcript joins the caller's voice channel and posts the live transcript
    into a text channel (the channel the command was used in, by default).
  * Utterances are batched, speaker display names are resolved, and the whole
    session is posted as one consolidated transcript when the bot stops.
"""

from __future__ import annotations

import asyncio
import queue
import time
from datetime import datetime, timezone
from multiprocessing import Queue as MPQueue
from pathlib import Path

import discord
import discord.ext.voice_recv
from discord.ext import commands

from bot.audio_sink import STTAudioSink
from config import discord_cfg, process_cfg, transcript_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

# Discord hard-caps messages at 2000 characters; stay under it.
SAFE_MESSAGE_BUDGET = 1800


class STTBot:
    """Wraps a commands.Bot with STT-specific lifecycle management."""

    def __init__(
        self,
        audio_queue: MPQueue,
        result_queue: MPQueue,
        command_queue: MPQueue,
    ) -> None:
        self._audio_q = audio_queue
        self._result_q = result_queue
        self._cmd_q = command_queue
        self._result_task: asyncio.Task | None = None

        # -- transcript session state --
        self._transcript_channel: discord.abc.Messageable | None = None
        self._session_lines: list[str] = []
        self._session_started_at: datetime | None = None
        self._pending: list[str] = []
        self._last_flush: float = 0.0
        self._speaker_names: dict[int, str] = {}
        # Set when the STT process reports its transcription backlog is empty.
        self._drained = asyncio.Event()

        intents = discord.Intents.default()
        intents.message_content = True

        self._bot = commands.Bot(
            command_prefix=discord_cfg.command_prefix,
            intents=intents,
        )
        self._register_events()
        self._register_commands()

    # -- public ------------------------------------

    def run(self) -> None:
        """Start the bot (blocking)."""
        if not discord_cfg.token:
            logger.critical("DISCORD_TOKEN is not set. Aborting.")
            return
        self._bot.run(discord_cfg.token, log_handler=None)

    # -- events ------------------------------------

    def _register_events(self) -> None:
        bot = self._bot

        @bot.event
        async def on_ready() -> None:
            logger.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)
            if self._result_task is None or self._result_task.done():
                self._result_task = bot.loop.create_task(self._poll_results())

        @bot.event
        async def on_voice_state_update(
            member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState,
        ) -> None:
            if before.channel == after.channel:
                return

            if bot.user and member.id == bot.user.id and before.channel:
                # We were disconnected (or Discord dropped the session): flush
                # whatever is buffered, then post the transcript we have.
                self._drained.clear()
                self._send_command("FLUSH_ALL", None)
                bot.loop.create_task(self._finalize_after_disconnect())
                return

            # Notify STT process when a user leaves or switches channels.
            if before.channel:
                self._send_command("LEAVE", member.id)

    # -- commands ----------------------------------

    def _register_commands(self) -> None:
        bot = self._bot

        @bot.command(name="transcript", aliases=["join", "start", "listen"])
        async def cmd_transcript(ctx: commands.Context) -> None:
            """Join your voice channel and post the transcript to a text channel."""
            if ctx.author.voice is None or ctx.author.voice.channel is None:
                await ctx.send("❌ You are not in a voice channel.")
                return

            voice_channel = ctx.author.voice.channel

            if transcript_cfg.channel_id:
                target = bot.get_channel(transcript_cfg.channel_id)
                if target is None:
                    await ctx.send(
                        "❌ TRANSCRIPT_CHANNEL_ID points at a channel I cannot see."
                    )
                    return
            else:
                target = ctx.channel

            already = (
                ctx.voice_client is not None
                and ctx.voice_client.channel == voice_channel
                and self._transcript_channel == target
            )
            if already:
                await ctx.send(f"🎙️ Already listening in **{voice_channel}**.")
                return

            # Close out a running session before opening a new one.
            if self._transcript_channel is not None:
                await self._close_session()

            self._transcript_channel = target
            self._session_lines = []
            self._pending = []
            self._session_started_at = datetime.now(timezone.utc)
            self._speaker_names = {}
            self._drained.clear()

            if ctx.voice_client is not None:
                await ctx.voice_client.move_to(voice_channel)
            else:
                vc = await voice_channel.connect(
                    cls=discord.ext.voice_recv.VoiceRecvClient,
                )
                vc.listen(STTAudioSink(self._audio_q))

            await target.send(
                f"🎙️ Listening in **{voice_channel}** — transcript starts here. "
                f"Use {discord_cfg.command_prefix}stop to finish."
            )
            logger.info("Transcribing %s into #%s", voice_channel, target)

        @bot.command(name="stop", aliases=["leave", "disconnect"])
        async def cmd_stop(ctx: commands.Context) -> None:
            """Stop listening once everything queued is transcribed, and leave."""
            if ctx.voice_client is None:
                await ctx.send("❌ I am not in a voice channel.")
                return

            # Clear before flushing so a fast drain signal cannot be missed.
            self._drained.clear()
            self._send_command("FLUSH_ALL", None)
            await ctx.send("⏳ Finishing the queued speech...")
            await self._await_drain()
            await ctx.voice_client.disconnect()
            await self._close_session()
            await ctx.send("👋 Left the voice channel.")

    def _send_command(self, command: str, payload: object) -> None:
        try:
            self._cmd_q.put_nowait((command, payload))
        except queue.Full:
            logger.warning("Command queue is full; dropped %s command.", command)
        except Exception as exc:
            logger.error("Failed to send %s command: %s", command, exc)

    # -- result polling ----------------------------

    async def _poll_results(self) -> None:
        """Drain the result queue and feed the transcript channel."""
        logger.info("Result polling task started.")
        while True:
            try:
                drained = False
                while True:
                    try:
                        result = self._result_q.get_nowait()
                    except queue.Empty:
                        break
                    drained = True
                    self._handle_result(result)

                await self._maybe_flush()

                if drained:
                    await asyncio.sleep(0)
                else:
                    await asyncio.sleep(process_cfg.result_poll_interval)
            except Exception as exc:
                logger.error("Result poll error: %s", exc)
                await asyncio.sleep(1)

    def _handle_result(self, result: dict) -> None:
        """Format one utterance and queue it for posting."""
        if result.get("event") == "drained":
            self._drained.set()
            return

        text = (result.get("text") or "").strip()
        if not text:
            return

        name = (result.get("name") or "").strip() or self._speaker_name(
            result.get("user_id")
        )
        logger.info(
            "📝 [%s] %s (latency %s)",
            name,
            text,
            result.get("latency"),
        )

        if self._transcript_channel is None:
            return  # no active session; the log line is the only trace

        line = f"**{name}** : {text}"
        self._session_lines.append(line)
        self._pending.append(line)

    def _speaker_name(self, user_id: object) -> str:
        """Resolve a Discord user id to a display name, with caching."""
        try:
            key = int(user_id)
        except (TypeError, ValueError):
            return "Unknown"

        cached = self._speaker_names.get(key)
        if cached is not None:
            return cached

        member = None
        guild = getattr(self._transcript_channel, "guild", None)
        if guild is not None:
            member = guild.get_member(key)
        if member is None:
            member = self._bot.get_user(key)

        name = None
        if member is not None:
            name = getattr(member, "display_name", None) or getattr(member, "name", None)

        name = name or f"User {key}"
        self._speaker_names[key] = name
        return name

    # -- transcript delivery -----------------------

    async def _maybe_flush(self, *, force: bool = False) -> None:
        """Post buffered lines, batched to stay clear of rate limits."""
        channel = self._transcript_channel
        if channel is None:
            return

        if not force:
            if not self._pending:
                return
            due = (
                time.monotonic() - self._last_flush
                >= transcript_cfg.flush_interval_seconds
                or len(self._pending) >= transcript_cfg.max_lines_per_message
            )
            if not due:
                return

        while self._pending:
            chunk = self._take_chunk()
            if not chunk:
                break
            self._last_flush = time.monotonic()
            try:
                await channel.send("\n".join(chunk))
            except discord.HTTPException as exc:
                logger.error("Could not post transcript chunk: %s", exc)
                break
            if not force:
                break

    def _take_chunk(self) -> list[str]:
        """Pop lines for one message: capped by line count and char budget."""
        chunk: list[str] = []
        size = 0
        while self._pending and len(chunk) < transcript_cfg.max_lines_per_message:
            nxt = self._pending[0]
            if chunk and size + len(nxt) + 1 > SAFE_MESSAGE_BUDGET:
                break
            chunk.append(self._pending.pop(0))
            size += len(nxt) + 1
        return chunk

    async def _await_drain(self, timeout: float = 7200.0) -> None:
        """Wait until the STT worker has transcribed everything queued.

        There is deliberately no short timeout: the whole point of the worker
        is that a slow model may take minutes, and dropping the tail of a
        conversation to save time would be the wrong trade.
        """
        try:
            await asyncio.wait_for(self._drained.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Gave up after %.0fs waiting for the transcription backlog.",
                timeout,
            )

    async def _finalize_after_disconnect(self) -> None:
        await self._await_drain()
        await self._close_session()

    async def _close_session(self) -> None:
        """Flush what is buffered, keep a local copy, and reset.

        The channel is already the transcript: lines are posted live as they
        are transcribed, so replaying them at the end would duplicate every
        word. Only the local markdown copy is written here.
        """
        channel = self._transcript_channel
        lines = self._session_lines
        started = self._session_started_at

        await self._maybe_flush(force=True)

        self._transcript_channel = None
        self._session_lines = []
        self._pending = []
        self._session_started_at = None

        if channel is None or not lines:
            return

        stamp = (started or datetime.now(timezone.utc)).astimezone()
        body = (
            f"# Transcription — {stamp:%Y-%m-%d %H:%M}\n\n"
            + "\n".join(lines)
            + "\n"
        )
        self._save_transcript(body, stamp)

    def _save_transcript(self, body: str, stamp: datetime) -> Path | None:
        """Write a local markdown copy; the channel post is the primary record."""
        try:
            out_dir = Path(transcript_cfg.save_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            path = out_dir / f"transcript-{stamp:%Y%m%d-%H%M%S}.md"
            path.write_text(body, encoding="utf-8")
            logger.info("Transcript saved to %s", path)
            return path
        except Exception as exc:
            logger.error("Could not save transcript: %s", exc)
            return None
