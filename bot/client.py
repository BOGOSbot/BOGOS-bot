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

from bot import pairing
from bot.audio_sink import STTAudioSink
from bot.cleanup import clean_lines, render_diff
from config import cleanup_cfg, discord_cfg, pairing_cfg, process_cfg, transcript_cfg
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
        # (speaker, verbatim text) for the cleanup pass, which needs the raw
        # words rather than the already-markdown-formatted lines.
        self._session_raw: list[tuple[str, str]] = []
        self._session_started_at: datetime | None = None
        self._pending: list[str] = []
        self._last_flush: float = 0.0
        self._speaker_names: dict[int, str] = {}
        # Set when the STT process reports its transcription backlog is empty.
        self._drained = asyncio.Event()
        # Feeds the idle-exit watchdog: a woken box should fall back asleep.
        self._last_activity = time.monotonic()

        intents = discord.Intents.default()
        intents.message_content = True
        # Needed to resolve sheet names to members for !pairing. Without it the
        # roster is empty and names are printed boldly instead of pinged.
        intents.members = True

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
            bot.loop.create_task(self._idle_exit_watchdog())

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

    # -- lifecycle ---------------------------------

    def _note_activity(self) -> None:
        """Reset the idle countdown."""
        self._last_activity = time.monotonic()

    async def _idle_exit_watchdog(self) -> None:
        """Exit once idle, so the host can sleep and stop billing.

        On an auto-pausing Upstash Box this is what returns the box to a paused
        state: the process must end for the container to go idle. A live
        session is never interrupted, however long it runs.
        """
        minutes = process_cfg.idle_exit_minutes
        if minutes <= 0:
            return
        limit = minutes * 60.0
        logger.info("Idle exit armed: %d minute(s) after the last activity.", minutes)

        while True:
            await asyncio.sleep(30)
            if self._transcript_channel is not None:
                continue  # someone is mid-session; never exit under them
            idle_for = time.monotonic() - self._last_activity
            if idle_for < limit:
                continue
            logger.info(
                "Idle for %.0f minute(s); exiting so the host can sleep.",
                idle_for / 60.0,
            )
            await self._bot.close()
            return

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
                await ctx.send(f"Already listening in **{voice_channel}**.")
                return

            # Check permissions BEFORE dialling. Without Connect, Discord never
            # completes the voice handshake and discord.py raises a bare
            # TimeoutError 30 seconds later, which explains nothing.
            me = getattr(ctx.guild, "me", None)
            if me is not None:
                perms = voice_channel.permissions_for(me)
                missing = [
                    label
                    for label, allowed in (
                        ("View Channel", perms.view_channel),
                        ("Connect", perms.connect),
                    )
                    if not allowed
                ]
                if missing:
                    await ctx.send(
                        f"I cannot join **{voice_channel}** — I am missing "
                        f"**{' and '.join(missing)}** on that voice channel. "
                        "A private channel needs an explicit allow for me: it "
                        "is a per-channel permission, not the server role."
                    )
                    return

            # Close out a running session before opening a new one.
            if self._transcript_channel is not None:
                await self._close_session()

            try:
                if ctx.voice_client is not None:
                    await ctx.voice_client.move_to(voice_channel)
                else:
                    vc = await voice_channel.connect(
                        cls=discord.ext.voice_recv.VoiceRecvClient,
                        timeout=60.0,
                    )
                    vc.listen(STTAudioSink(self._audio_q))
            except asyncio.TimeoutError:
                logger.error("Voice connect to %s timed out.", voice_channel)
                await ctx.send(
                    f"Timed out joining **{voice_channel}**. Usual causes: no "
                    "Connect permission there, the channel is full, or a stale "
                    "voice session (restart me)."
                )
                return
            except discord.Forbidden:
                logger.error("Discord refused voice connect to %s.", voice_channel)
                await ctx.send(
                    f"Discord refused the connection to **{voice_channel}**."
                )
                return
            except Exception as exc:
                logger.error("Voice connect to %s failed: %s", voice_channel, exc)
                await ctx.send(f"Could not join **{voice_channel}**: {exc}")
                return

            # Only open the session once the bot is actually in the channel.
            self._transcript_channel = target
            self._session_lines = []
            self._session_raw = []
            self._pending = []
            self._session_started_at = datetime.now(timezone.utc)
            self._speaker_names = {}
            self._drained.clear()
            self._note_activity()

            await target.send(
                f"Listening in **{voice_channel}**. "
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
            await ctx.send("Stopped listening. Finishing the queued speech.")
            await self._await_drain()
            await ctx.voice_client.disconnect()
            await self._post_corrections()
            await self._close_session()
            await ctx.send("Left the voice channel.")
            self._note_activity()

        @bot.command(name="pairing", aliases=["table", "sheet"])
        async def cmd_pairing(ctx: commands.Context, link: str = "") -> None:
            """Report every team tab of a pairing workbook, in one message.

            Takes a Sheets link; with none, PAIRING_SHEET_ID is used. One
            workbook is one tournament, one tab is one opposing team.
            """
            target = link.strip() or pairing_cfg.sheet_id
            if not target:
                await ctx.send("Usage: !pairing <lien google sheets>")
                return

            async with ctx.typing():
                try:
                    # urllib blocks; keep it off the event loop.
                    sheet_id = pairing.sheet_id_from(target)
                    tabs, skipped = await asyncio.to_thread(
                        pairing.collect, sheet_id
                    )
                except Exception as exc:
                    logger.exception("Could not read the pairing sheet.")
                    await ctx.send(f"❌ Could not read the pairing sheet: {exc}")
                    return

                if skipped:
                    logger.debug("Pairing tabs skipped: %s", ", ".join(skipped))

                # Resolve sheet names against the live roster, so a rename or
                # a new player needs no configuration. Failure is survivable:
                # names simply come out bold instead of pinged.
                resolver = pairing.Resolver()
                if ctx.guild is not None:
                    try:
                        roster = list(ctx.guild.members)
                        if not roster:
                            roster = [
                                member
                                async for member in ctx.guild.fetch_members(limit=None)
                            ]
                        resolver.members = pairing.index_members(
                            (m.id, m.nick, m.global_name, m.name) for m in roster
                        )
                    except Exception:
                        logger.warning(
                            "Could not read the guild roster; names stay unpinged."
                        )

            await ctx.send(pairing.render(tabs, resolver))
            self._note_activity()

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
        self._session_raw.append((name, text))
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
        await self._post_corrections()
        await self._close_session()

    async def _post_corrections(self) -> None:
        """One LLM pass over the session, shown as a diff, never applied silently.

        The verbatim transcript is already in the channel; only the lines that
        actually changed are posted, so nothing is repeated.
        """
        channel = self._transcript_channel
        pairs = list(self._session_raw)
        if not cleanup_cfg.enabled or channel is None or not pairs:
            return

        raw_lines = [text for _name, text in pairs]
        try:
            corrected = await asyncio.get_running_loop().run_in_executor(
                None, clean_lines, raw_lines
            )
        except Exception as exc:
            logger.error("Correction pass error: %s", exc)
            return
        if corrected is None:
            return

        changed = [
            f"**{name}**: {render_diff(text, fixed.strip())}"
            for (name, text), fixed in zip(pairs, corrected)
            if fixed.strip() and fixed.strip() != text
        ]
        if not changed:
            return

        header = f"✎ {len(changed)} correction(s) — ~~removed~~, *added*:"
        batch: list[str] = [header]
        size = len(header)
        for line in changed:
            if size + len(line) + 1 > SAFE_MESSAGE_BUDGET and len(batch) > 1:
                await channel.send("\n".join(batch))
                batch, size = [], 0
            batch.append(line)
            size += len(line) + 1
        try:
            await channel.send("\n".join(batch))
        except discord.HTTPException as exc:
            logger.error("Could not post corrections: %s", exc)

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
        self._session_raw = []
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
