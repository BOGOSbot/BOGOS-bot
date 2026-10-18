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
import json
import queue
import re

import time
from datetime import datetime, timezone
from multiprocessing import Queue as MPQueue
from pathlib import Path

import discord
import discord.ext.voice_recv
from discord.ext import commands

import recording
from bot import pairing
from bot.audio_sink import STTAudioSink
from bot.cleanup import clean_lines, render_diff
from bot.recorder import ChannelRecorder
from config import (
    audio_cfg,
    cleanup_cfg,
    discord_cfg,
    process_cfg,
    transcript_cfg,
)
from utils.logging import get_logger

logger = get_logger(__name__)


STATUS_FILE = Path(__file__).resolve().parents[1] / "status.json"


def write_state(state: str) -> None:
    """Publish what the bot is doing, for the widget to display.

    Kept as a file rather than an endpoint: the widget is a separate process on
    the same machine, and a file survives a widget restart, costs nothing to
    read, and cannot be reached from off the machine.
    """
    try:
        STATUS_FILE.write_text(
            json.dumps({"state": state, "at": time.time()}), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("Could not write the status file: %s", exc)


def _short_model(model: str) -> str:
    """mistralai/voxtral-small-24b-2507-stt -> voxtral, for a readable label."""
    tail = (model or "").rsplit("/", 1)[-1]
    return tail.split("-")[0] or "autre modèle"

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
        # Corpus capture for the current session; None unless RECORD_DIR is set.
        self._recorder: ChannelRecorder | None = None
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
        # Nothing is posted while the session runs. Every utterance is kept
        # here with both readings, and the whole session is reconciled and
        # posted once, at the end.
        self._entries: list[dict] = []
        # The file being appended to as chunks are transcribed, so a crash or a
        # sleep costs at most the chunk in flight.
        self._session_path: Path | None = None
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
        write_state("sleeping")
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
                    # Asked per session rather than read once at import, so
                    # the widget's switch applies without a restart. A no-op
                    # unless recording is on.
                    self._recorder = ChannelRecorder(
                        recording.current().session_dir,
                        audio_cfg.output_sample_rate,
                    )
                    vc.listen(STTAudioSink(self._audio_q, self._recorder))
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
            write_state("listening")
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
            write_state("emptying")
            await self._await_drain()
            write_state("reconciling")
            await ctx.voice_client.disconnect()
            await self._post_corrections()
            await self._close_session()
            await ctx.send("Left the voice channel.")
            self._note_activity()

        @bot.command(name="pairing", aliases=["table", "sheet"])
        async def cmd_pairing(ctx: commands.Context, link: str = "") -> None:
            """Report every team tab of a pairing workbook, in one message.

            Takes a Sheets link: one workbook is one tournament, one tab is one
            opposing team. There is no configured fallback sheet, because there
            is one workbook per tournament - the link is the input.
            """
            if not link.strip():
                await ctx.send("Commande: !pairing <lien vers une fiche de pairing>")
                return

            async with ctx.typing():
                try:
                    # urllib blocks; keep it off the event loop.
                    sheet_id = pairing.sheet_id_from(link.strip())
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

        # One line per chunk, one reading. A chunk is several minutes of one
        # speaker, so "**Name** :" is the whole label needed; there is no second
        # reading to bracket and no reconciler to move the speaker's name out of
        # the way. The blank line between entries is what keeps Discord from
        # running them together into one paragraph.
        line = f"**{name}** : {text}"
        self._session_lines.append(line)
        self._session_raw.append((name, text))
        self._entries.append(
            {
                "name": name,
                "text": text,
                "seconds": result.get("seconds"),
                "line": line,
            }
        )
        # Written now, not at the end. A session can run for hours, and the
        # transcript that exists is worth more than the one still in memory if
        # the process dies or the machine sleeps.
        self._append_to_session(line)

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
        if self._transcript_channel is None:
            return  # the stop command already closed this session
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
        """Post the transcript, which was written as it arrived.

        Nothing is posted while the session runs, because a transcript
        interrupted every few minutes reads worse than the same text in one
        piece. Each chunk is written to disk the moment it is transcribed, so
        the only work left at the stop is to name the file and hand it over.
        """
        channel = self._transcript_channel
        started = self._session_started_at
        entries = list(self._entries)
        logger.info("Closing the session with %d chunk(s).", len(entries))

        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

        self._transcript_channel = None
        self._session_lines = []
        self._session_raw = []
        self._entries = []
        self._pending = []
        self._session_started_at = None
        self._session_path = None

        if channel is None:
            write_state("sleeping")
            logger.warning("No channel to post the session to: it is lost.")
            return

        if not entries:
            # A stop that produced nothing still has to say so. Returning
            # silently makes a working bot indistinguishable from a broken one,
            # and leaves the reader unable to tell whether they were heard.
            try:
                await channel.send(
                    "Nothing transcribed — no speech reached me in that "
                    "session. Check that you were unmuted and in the channel."
                )
            except discord.HTTPException as exc:
                logger.error("Could not post the empty-session notice: %s", exc)
            write_state("sleeping")
            return

        stamp = (started or datetime.now(timezone.utc)).astimezone()
        text = [entry["line"] for entry in entries]
        path = self._save_session(entries, text, stamp)
        await self._post_transcript(channel, path, text, entries)

    def _append_to_session(self, line: str) -> None:
        """Write one chunk's transcript the moment it exists."""
        try:
            out_dir = Path(transcript_cfg.save_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            if self._session_path is None:
                stamp = (
                    self._session_started_at or datetime.now(timezone.utc)
                ).astimezone()
                self._session_path = (
                    out_dir / f"transcription-{stamp:%Y-%m-%d-%H%M}.md"
                )
                self._session_path.write_text(
                    f"# Transcription — {stamp:%Y-%m-%d %H:%M}\n\n", encoding="utf-8"
                )
            with self._session_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n\n")
        except Exception as exc:
            logger.error("Could not append to the transcript: %s", exc)

    async def _post_transcript(
        self,
        channel,
        path: Path | None,
        lines: list[str],
        entries: list[dict],
    ) -> None:
        """Post the session as a dated file rather than a wall of text.

        A transcript pasted into the channel is unscrollable, splits across
        messages, and loses its formatting. A file is one message, opens on its
        own, and stays readable months later. The channel gets the file and a
        line saying what is in it.
        """
        summary = [f"**Transcription** · {len(entries)} chunk(s)"]
        if path is None:
            # Writing the file failed, so post the text rather than nothing.
            await self._post_lines(channel, lines)
            return
        try:
            await channel.send(
                " · ".join(summary),
                file=discord.File(path, filename=path.name),
            )
        except (discord.HTTPException, OSError) as exc:
            logger.error("Could not post the transcript file: %s", exc)
            await self._post_lines(channel, lines)

    async def _post_lines(self, channel, lines: list[str]) -> None:
        """Last resort: post the text, batched under the message cap."""
        batch: list[str] = []
        size = 0
        for line in lines:
            if size + len(line) + 1 > SAFE_MESSAGE_BUDGET and batch:
                try:
                    await channel.send("\n".join(batch))
                except discord.HTTPException as exc:
                    logger.error("Could not post the transcript: %s", exc)
                    return
                batch, size = [], 0
            batch.append(line)
            size += len(line) + 1
        if batch:
            try:
                await channel.send("\n".join(batch))
            except discord.HTTPException as exc:
                logger.error("Could not post the transcript: %s", exc)

    def _save_session(
        self,
        entries: list[dict],
        lines: list[str],
        stamp: datetime,
    ) -> Path | None:
        """Write the session's chunks, one block per chunk, with their metadata."""
        try:
            out_dir = Path(transcript_cfg.save_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = f"transcription-{stamp:%Y-%m-%d-%H%M}"
            # No counts line and no legend: one model read this, so there is
            # nothing to explain. Blank lines between entries are what matter
            # here: Discord renders markdown, and consecutive lines with
            # nothing between them collapse into a single paragraph.
            (out_dir / f"{stem}.md").write_text(
                f"# Transcription \u2014 {stamp:%Y-%m-%d %H:%M}\n\n"
                + "\n\n".join(lines)
                + "\n",
                encoding="utf-8",
            )
            payload = {
                "started": stamp.isoformat(),
                "utterances": len(entries),
                "final_lines": len(lines),
                "entries": entries,
            }
            (out_dir / f"{stem}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Session saved to %s", out_dir / stem)
            return out_dir / f"{stem}.md"
        except Exception as exc:
            logger.error("Could not save the session: %s", exc)
            return None

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
