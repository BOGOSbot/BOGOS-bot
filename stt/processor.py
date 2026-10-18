"""
STT Process -- the isolated heavy-compute worker.

Runs in its own OS process (via multiprocessing) so the Discord bot's event
loop never blocks on inference.

Pipeline per frame (32 ms):
    IPC Queue -> UserStateManager -> Silero VAD -> Utterance Queue
              -> transcriber worker pool -> ordered emitter -> Result Queue

A pool of worker threads drains the utterance queue in parallel. Because
they finish out of order, results are re-sequenced before they are emitted:
a fast sentence must not appear before the slower one that preceded it.
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time
import wave
from multiprocessing import Queue as MPQueue
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

import recording
from config import api_cfg, audio_cfg, process_cfg, stt_cfg, vad_cfg
from stt import artifacts
from utils.logging import get_logger

logger = get_logger(__name__)

DRAINED_EVENT = "drained"


def _dbfs(samples: np.ndarray) -> float:
    """Level of an int16 signal in dBFS (-99 when silent)."""
    if samples.size == 0:
        return -99.0
    rms = float(np.sqrt((samples.astype(np.float64) ** 2).mean()))
    if rms <= 0.0:
        return -99.0
    return 20.0 * math.log10(rms / 32768.0)


def _normalise(samples: np.ndarray, target_dbfs: float, max_gain_db: float) -> bytes:
    """Lift an utterance toward a consistent level so quiet speakers are not penalised.

    Speech in the captured dumps ranged from -52 to -15 dBFS; the quiet end is
    a lot of lost signal for the model to work with.
    """
    if samples.size == 0 or target_dbfs >= 0.0:
        return samples.tobytes()
    gain_db = min(target_dbfs - _dbfs(samples), max_gain_db)
    if abs(gain_db) < 1.0:
        return samples.tobytes()
    gain = 10.0 ** (gain_db / 20.0)
    scaled = np.clip(np.rint(samples.astype(np.float64) * gain), -32768, 32767)
    return scaled.astype(np.int16).tobytes()
# Give up waiting for the backlog rather than hanging !stop forever.
_DRAIN_TIMEOUT = 300.0


class STTProcessor:
    """Orchestrates VAD + transcription for all active users."""

    def __init__(
        self,
        audio_queue: MPQueue,
        result_queue: MPQueue,
        command_queue: MPQueue,
    ) -> None:
        self._audio_q = audio_queue
        self._result_q = result_queue
        self._cmd_q = command_queue

        # Heavy imports kept local to this process
        from stt.vad import VADWrapper
        from stt.user_state import UserStateManager

        self._vad = VADWrapper()

        # One transcriber, one reading. A whole chunk of speech carries enough
        # context that the model stops guessing at the proper nouns, which is
        # what the second model and the reconciler were compensating for.
        if api_cfg.backend == "openrouter":
            from stt.gemini_transcriber import GeminiTranscriber

            self._transcriber = GeminiTranscriber()
        else:
            from stt.transcriber import Transcriber

            self._transcriber = Transcriber()

        from stt.chunker import AudioChunker

        self._chunker = AudioChunker(
            stt_cfg.chunk_seconds, stt_cfg.chunk_max_seconds
        )

        self._users = UserStateManager(vad_iterator_factory=self._vad.create_iterator)
        self._dump_n = 0
        self._names: dict[int, str] = {}
        self._dropped_quiet = 0
        self._dropped_short = 0

        # Utterances waiting for a worker: (seq, user_id, pcm). Unbounded on
        # purpose: falling behind must delay the transcript, never lose speech.
        self._utterances: "queue.Queue[Optional[Tuple[int, int, bytes, float]]]" = queue.Queue()
        self._workers: list[threading.Thread] = []
        self._warned_at = 0

        # Ordered emission.
        self._seq = 0
        self._seq_lock = threading.Lock()
        self._order_lock = threading.Lock()
        self._ordered: dict[int, Optional[dict]] = {}
        self._emit_next = 1

    # -- lifecycle --------------------------------

    def run(self) -> None:
        """Block forever, processing audio from the IPC queue."""
        self._start_workers()
        logger.info(
            "STT Processor ready (PID %d), %d transcription worker(s).",
            os.getpid(),
            len(self._workers),
        )

        while True:
            self._handle_commands()
            self._process_audio()
            self._flush_stale_speech()
            self._cleanup_inactive_users()

    def _start_workers(self) -> None:
        for i in range(max(1, process_cfg.transcribe_workers)):
            thread = threading.Thread(
                target=self._transcribe_loop, name=f"STT-Worker-{i}", daemon=True
            )
            thread.start()
            self._workers.append(thread)

    # -- ordering ---------------------------------

    def _reserve_seq(self) -> int:
        with self._seq_lock:
            self._seq += 1
            return self._seq

    def _deliver(self, seq: int, result: Optional[dict]) -> None:
        """Hand results to the client in sequence order, not completion order."""
        with self._order_lock:
            self._ordered[seq] = result
            while self._emit_next in self._ordered:
                item = self._ordered.pop(self._emit_next)
                self._emit_next += 1
                if item is not None:
                    self._put_result(item)

    def _transcribe_loop(self) -> None:
        """One worker: pull an utterance, transcribe it, deliver it in order."""
        while True:
            item = self._utterances.get()
            if item is None:
                self._utterances.task_done()
                return

            seq, user_id, audio, seconds = item
            try:
                result = self._transcriber.transcribe(user_id, audio)
                if result and not self._plausible(result, audio):
                    result = None  # Guard 3: invented, whatever its level.
                if result and artifacts.is_artifact(result.get("text", "")):
                    logger.warning(
                        "Discarded memorised ASR artifact: %s",
                        (result.get("text") or "")[:70],
                    )
                    result = None  # Guard 4: a subtitle credit, never speech.
                if result:
                    result["seq"] = seq
                    result["seconds"] = round(seconds, 1)
                    result.setdefault("name", self._names.get(user_id, ""))
                else:
                    logger.warning(
                        "No transcript for a %.0fs chunk from user %s.",
                        seconds,
                        user_id,
                    )
                self._deliver(seq, result)
            except Exception as exc:
                logger.error("Transcription failed for user %s: %s", user_id, exc)
                self._deliver(seq, None)
            finally:
                self._utterances.task_done()

    def _plausible(self, result: dict, audio: bytes) -> bool:
        """Reject a transcript nobody could have spoken in that much time.

        Measured hallucinations recite training data at absurd rates: a 0.6 s
        clip came back as a sixteen-word sentence. Real speech is a few words
        per second, and energy does not separate the two.
        """
        seconds = max(
            len(audio) / (audio_cfg.output_sample_rate * audio_cfg.sample_width),
            0.1,
        )
        words = len((result.get("text") or "").split())
        limit = max(
            stt_cfg.min_words_grace, stt_cfg.max_words_per_second * seconds
        )
        if words > limit:
            logger.warning(
                "Discarded likely hallucination: %d words from %.2fs of audio: %s",
                words,
                seconds,
                (result.get("text") or "")[:70],
            )
            return False
        return True

    # -- drain signalling -------------------------

    def _request_drain(self) -> None:
        """Reserve a sequence number past everything queued, then wait."""
        seq = self._reserve_seq()
        threading.Thread(
            target=self._await_empty, args=(seq,), name="STT-Drain", daemon=True
        ).start()

    def _await_empty(self, seq: int) -> None:
        deadline = time.monotonic() + _DRAIN_TIMEOUT
        while self._utterances.unfinished_tasks > 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        pending = self._utterances.unfinished_tasks
        if pending:
            logger.warning("Drain timed out with %d utterance(s) still queued.", pending)
        self._deliver(seq, {"event": DRAINED_EVENT})

    def _await_backlog(self, timeout: float = 120.0) -> None:
        deadline = time.monotonic() + timeout
        while self._utterances.unfinished_tasks > 0 and time.monotonic() < deadline:
            time.sleep(0.05)

    def _enqueue(self, user_id: int, audio: bytes) -> None:
        self._dump(user_id, audio)

        # Guard 0: a segment shorter than the VAD padding cannot hold a word.
        # 8 of 77 recorded utterances were under 0.5s; with 200ms of pad on
        # each side that leaves under 100ms of anything, which is a click or
        # a breath - and the model answers those with something fluent.
        seconds = len(audio) / (
            audio_cfg.output_sample_rate * audio_cfg.sample_width
        )
        if seconds < stt_cfg.min_utterance_seconds:
            self._dropped_short += 1
            if self._dropped_short % 10 == 1:
                logger.info(
                    "Dropped %d too-short utterance(s); last was %.2fs.",
                    self._dropped_short,
                    seconds,
                )
            return

        # Guard 1: silence is not speech. ASR models answer silence with
        # invented fluent text rather than an empty string.
        samples = np.frombuffer(audio, dtype=np.int16)
        level = _dbfs(samples)
        if level < stt_cfg.min_speech_dbfs:
            self._dropped_quiet += 1
            if self._dropped_quiet % 10 == 1:
                logger.info(
                    "Dropped %d near-silent utterance(s); last was %.1f dBFS.",
                    self._dropped_quiet,
                    level,
                )
            return

        # Guard 2: level the utterance before it is sent.
        audio = _normalise(samples, stt_cfg.normalize_dbfs, stt_cfg.max_gain_db)

        chunk = self._chunker.add(user_id, audio)
        if chunk is not None:
            self._emit_chunk(chunk)

    def _emit_chunk(self, chunk) -> None:
        """Queue a finished chunk for transcription."""
        depth = self._utterances.qsize()
        if depth >= 5 and depth - self._warned_at >= 5:
            self._warned_at = depth
            logger.warning(
                "%d chunks queued; the transcript is falling behind real time "
                "but nothing is being lost.",
                depth,
            )
        self._utterances.put(
            (self._reserve_seq(), chunk.user_id, chunk.pcm, chunk.seconds)
        )

    # -- internals --------------------------------

    def _handle_commands(self) -> None:
        """Drain the command queue (non-blocking)."""
        while True:
            try:
                cmd, data = self._cmd_q.get_nowait()
            except queue.Empty:
                return
            except Exception as exc:
                logger.error("Command queue error: %s", exc)
                return

            if cmd == "LEAVE":
                state = self._users.remove(data)
                if state:
                    self._flush_state(state, "leave")
            elif cmd == "FLUSH_ALL":
                self._flush_everything("flush-all")
                self._request_drain()
            elif cmd == "SHUTDOWN":
                logger.info("Received SHUTDOWN command.")
                self._flush_everything("shutdown")
                self._await_backlog()
                raise SystemExit(0)

    def _process_audio(self, timeout: float = 0.1) -> bool:
        """Pull one chunk from the audio queue and feed it through VAD.

        Returns whether an item was there, so a caller can tell a drained
        queue from a slow one.
        """
        try:
            item = self._audio_q.get(timeout=timeout)
        except queue.Empty:
            return False
        except Exception as exc:
            logger.error("Audio queue error: %s", exc)
            return False

        # Accept both the sink's (id, name, pcm) triple and a bare (id, pcm).
        if len(item) == 3:
            user_id, display_name, pcm_data = item
        else:
            user_id, pcm_data = item
            display_name = ""
        if display_name:
            self._names[user_id] = display_name

        state = self._users.get_or_create(user_id)
        state.raw_buffer.extend(pcm_data)

        frame_bytes = vad_cfg.frame_bytes
        max_bytes = int(
            vad_cfg.max_utterance_seconds
            * audio_cfg.output_sample_rate
            * audio_cfg.sample_width
        )

        while len(state.raw_buffer) >= frame_bytes:
            frame = bytes(state.raw_buffer[:frame_bytes])
            del state.raw_buffer[:frame_bytes]

            tensor = self._vad.frame_to_tensor(frame)
            state.vad_iterator(tensor, return_seconds=True)
            is_speech = state.vad_iterator.triggered

            if is_speech:
                if not state.is_speaking:
                    for prev_frame in state.ring_buffer.drain():
                        state.speech_buffer.extend(prev_frame)
                state.speech_buffer.extend(frame)

                if len(state.speech_buffer) >= max_bytes:
                    self._enqueue(user_id, bytes(state.reset_speech()))
            else:
                if state.is_speaking:
                    self._enqueue(user_id, bytes(state.reset_speech()))
                state.ring_buffer.append(frame)

        return True

    def _drain_audio(self) -> bool:
        """Consume every frame the sink has already pushed. True if it moved.

        The command loop reads the queue one item per turn, so a FLUSH_ALL can
        arrive with audio still queued behind it.
        """
        moved = False
        idle = 0
        while idle < 3:
            if self._process_audio(timeout=0.0):
                moved = True
                idle = 0
            else:
                idle += 1
                time.sleep(0.02)
        return moved

    def _flush_everything(self, reason: str) -> None:
        """Flush the speech the sink already pushed, not just what the VAD saw.

        FLUSH_ALL is handled at the top of the loop, but frames the sink queued
        after the processor's last read are still behind it. Flushing first
        would leave them to be VAD'd into a chunker that had already been
        emptied, and the drain signal would fire before those utterances
        existed: the tail of the session would never be transcribed, and the
        client would close the file without it. Draining first, then flushing,
        then checking again, closes that window.
        """
        for _ in range(20):
            moved = self._drain_audio()
            self._flush_all(reason)
            if not moved and self._audio_q.empty():
                return
        logger.warning("Flush settled with audio still arriving; not waiting.")

    def _flush_state(self, state, reason: str) -> None:
        """Hand any buffered speech to a worker before dropping a state."""
        if not state.is_speaking:
            return
        logger.info("Flushed user %s speech due to %s.", state.user_id, reason)
        self._enqueue(state.user_id, bytes(state.reset_speech()))

    def _flush_all(self, reason: str) -> None:
        for state in self._users.remove_all():
            self._flush_state(state, reason)
        for chunk in self._chunker.flush():
            self._emit_chunk(chunk)

    def _flush_stale_speech(self) -> None:
        for state in self._users.stale_speech_states():
            self._flush_state(state, "speech inactivity")

    def _cleanup_inactive_users(self) -> None:
        for state in self._users.cleanup_inactive():
            self._flush_state(state, "user timeout")

    def _dump(self, user_id: int, audio: bytes) -> None:
        """Persist one utterance as WAV -- byte-for-byte what the model hears."""
        # Asked per utterance, so switching recording off stops the very next
        # one rather than the next restart.
        dump_dir = recording.current().dump_dir
        if not dump_dir:
            return
        try:
            out = Path(dump_dir)
            out.mkdir(parents=True, exist_ok=True)
            self._dump_n += 1
            path = out / f"{self._dump_n:04d}-u{user_id}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(audio_cfg.output_channels)
                w.setsampwidth(audio_cfg.sample_width)
                w.setframerate(audio_cfg.output_sample_rate)
                w.writeframes(audio)
        except Exception as exc:
            logger.error("Utterance dump failed: %s", exc)

    def _put_result(self, result: dict) -> None:
        try:
            self._result_q.put_nowait(result)
        except queue.Full:
            logger.warning(
                "Result queue full; dropped transcription for user %s.",
                result.get("user_id"),
            )
        except Exception as exc:
            logger.error("Result queue error: %s", exc)


# -- Process entry point -------------------------


def run_stt_process(
    audio_queue: MPQueue,
    result_queue: MPQueue,
    command_queue: MPQueue,
) -> None:
    """
    Top-level function spawned by multiprocessing.Process.

    All heavy model loading happens here (inside the child process)
    to avoid CUDA context issues.
    """
    try:
        processor = STTProcessor(audio_queue, result_queue, command_queue)
        processor.run()
    except SystemExit:
        logger.info("STT process exiting gracefully.")
    except KeyboardInterrupt:
        logger.info("STT process interrupted.")
    except Exception as exc:
        logger.critical("STT process crashed: %s", exc, exc_info=True)
        raise
