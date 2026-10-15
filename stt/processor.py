"""
STT Process -- the isolated heavy-compute worker.

Runs in its own OS process (via multiprocessing) so the Discord bot's event
loop never blocks on inference.

Pipeline per frame (32 ms):
    IPC Queue -> UserStateManager -> Silero VAD -> Utterance Queue
              -> Faster-Whisper (worker thread) -> Result Queue

Transcription runs on its own thread and drains an utterance queue, so a slow
model never stalls audio intake. Utterances wait in line and are transcribed at
whatever pace the CPU manages; speech is delayed, never dropped.
"""

from __future__ import annotations

import os
import queue
import threading
from multiprocessing import Queue as MPQueue
from typing import Optional, Tuple

from config import audio_cfg, vad_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

# Sentinel queued after a FLUSH_ALL so the client can tell when the backlog is
# empty. It rides the same FIFO as the utterances, so ordering is guaranteed.
DRAIN = "__DRAIN__"
DRAINED_EVENT = "drained"


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
        from stt.transcriber import Transcriber
        from stt.user_state import UserStateManager

        self._vad = VADWrapper()
        self._transcriber = Transcriber()
        self._users = UserStateManager(vad_iterator_factory=self._vad.create_iterator)

        # Utterances waiting for the model. Unbounded on purpose: when the CPU
        # cannot keep up the transcript falls behind, but no speech is lost.
        self._utterances: "queue.Queue[Optional[Tuple[int, bytes]]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._warned_at = 0

    # -- lifecycle --------------------------------

    def run(self) -> None:
        """Block forever, processing audio from the IPC queue."""
        self._start_worker()
        logger.info("STT Processor ready (PID %d).", os.getpid())

        while True:
            self._handle_commands()
            self._process_audio()
            self._flush_stale_speech()
            self._cleanup_inactive_users()

    def _start_worker(self) -> None:
        self._worker = threading.Thread(
            target=self._transcribe_loop,
            name="STT-Transcriber",
            daemon=True,
        )
        self._worker.start()

    def _transcribe_loop(self) -> None:
        """Drain the utterance queue; the only thread that touches the model."""
        while True:
            item = self._utterances.get()
            if item is None:
                self._utterances.task_done()
                return

            user_id, audio = item
            try:
                if user_id == DRAIN:
                    self._put_result({"event": DRAINED_EVENT})
                    continue
                result = self._transcriber.transcribe(user_id, audio)
                if result:
                    self._put_result(result)
            except Exception as exc:
                logger.error("Transcription failed for user %s: %s", user_id, exc)
            finally:
                self._utterances.task_done()

    def _enqueue(self, user_id: int, audio: bytes) -> None:
        depth = self._utterances.qsize()
        if depth >= 25 and depth - self._warned_at >= 25:
            self._warned_at = depth
            logger.warning(
                "%d utterances queued; the transcript is falling behind "
                "real time but nothing is being lost.",
                depth,
            )
        self._utterances.put((user_id, audio))

    def _await_backlog(self) -> None:
        """Wait for the worker to finish everything queued."""
        pending = self._utterances.unfinished_tasks
        if not pending:
            return
        logger.info("Waiting for %d queued utterance(s) to transcribe...", pending)
        self._utterances.join()
        logger.info("Transcription backlog drained.")

    def _stop_worker(self) -> None:
        if self._worker is None:
            return
        self._utterances.put(None)
        self._worker.join(timeout=30)

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
                self._flush_all("flush-all")
                # Signal completion after everything already queued.
                self._utterances.put((DRAIN, b""))
            elif cmd == "SHUTDOWN":
                logger.info("Received SHUTDOWN command.")
                self._flush_all("shutdown")
                self._await_backlog()
                raise SystemExit(0)

    def _process_audio(self) -> None:
        """Pull one chunk from the audio queue and feed it through VAD."""
        try:
            user_id, pcm_data = self._audio_q.get(timeout=0.1)
        except queue.Empty:
            return
        except Exception as exc:
            logger.error("Audio queue error: %s", exc)
            return

        state = self._users.get_or_create(user_id)
        state.raw_buffer.extend(pcm_data)

        frame_bytes = vad_cfg.frame_bytes
        max_bytes = int(
            vad_cfg.max_utterance_seconds
            * audio_cfg.output_sample_rate
            * audio_cfg.sample_width
        )

        while len(state.raw_buffer) >= frame_bytes:
            # Slice one frame
            frame = bytes(state.raw_buffer[:frame_bytes])
            del state.raw_buffer[:frame_bytes]

            # VAD inference
            tensor = self._vad.frame_to_tensor(frame)
            state.vad_iterator(tensor, return_seconds=True)
            is_speech = state.vad_iterator.triggered

            if is_speech:
                # On speech onset, prepend the ring-buffer context
                if not state.is_speaking:
                    for prev_frame in state.ring_buffer.drain():
                        state.speech_buffer.extend(prev_frame)
                state.speech_buffer.extend(frame)

                # Stream a long monologue out in bounded chunks instead of
                # holding everything until the speaker finally pauses.
                if len(state.speech_buffer) >= max_bytes:
                    self._enqueue(user_id, bytes(state.reset_speech()))
            else:
                if state.is_speaking:
                    # End-of-speech -> hand the utterance to the worker
                    self._enqueue(user_id, bytes(state.reset_speech()))
                # Keep recent silence in ring buffer
                state.ring_buffer.append(frame)

    def _flush_state(self, state, reason: str) -> None:
        """Hand any buffered speech to the worker before dropping a state."""
        if not state.is_speaking:
            return
        logger.info("Flushed user %s speech due to %s.", state.user_id, reason)
        self._enqueue(state.user_id, bytes(state.reset_speech()))

    def _flush_all(self, reason: str) -> None:
        for state in self._users.remove_all():
            self._flush_state(state, reason)

    def _flush_stale_speech(self) -> None:
        for state in self._users.stale_speech_states():
            self._flush_state(state, "speech inactivity")

    def _cleanup_inactive_users(self) -> None:
        for state in self._users.cleanup_inactive():
            self._flush_state(state, "user timeout")

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
