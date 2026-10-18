"""Collect utterances into chunks large enough to transcribe well.

A transcription per utterance wastes the model. Gemini reads a whole exchange
better than a sentence: given five minutes it keeps the thread, spells the game's
proper nouns the way the speaker did, and costs the same per second of audio as
it would on fragments. It also removes the reason the pipeline once carried two
models and a reconciler, because the reading stops being wrong in the first
place.

The size is set by the provider, not by preference. A request body is capped at
20 MB and base64 inflates by a third, so 16 kHz mono tops out near 7.8 minutes.
Five minutes encodes to 12.8 MB, which leaves room, and it is long enough that
the model sees a conversation rather than a line.

Chunks are kept per speaker. Concatenating two people into one stream would let
the model read the dialogue, but it would also lose who said what, and a
transcript without names is not worth much in a Discord log.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field

from config import audio_cfg, stt_cfg
from utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class Chunk:
    """A slice of one speaker's speech, ready to send."""

    user_id: int
    seq: int
    pcm: bytes
    seconds: float
    utterances: int
    # When this chunk's first utterance arrived, as a wall-clock epoch. Chunks
    # are built per speaker and each fills at its own rate, so they come out of
    # the pipeline in completion order, not in the order they were spoken.
    # This is what puts the session back in order at the end.
    started_at: float = 0.0


@dataclass
class _Pending:
    seq: int = 0
    parts: list[bytes] = field(default_factory=list)
    seconds: float = 0.0
    utterances: int = 0
    started_at: float = 0.0


def _silence(seconds: float) -> bytes:
    frames = int(seconds * audio_cfg.output_sample_rate) * audio_cfg.sample_width
    return b"\x00" * max(frames, 0)


class AudioChunker:
    """Accumulate one speaker's utterances until there is enough to send.

    add() returns a Chunk the moment the buffer is full, so transcription starts
    while the session is still running: a two-hour game is twenty-four chunks
    settled as they arrive, not one wait at the end.
    """

    def __init__(
        self,
        target_seconds: float,
        max_seconds: float,
        max_span_seconds: float = 0.0,
    ) -> None:
        self._target = max(target_seconds, 10.0)
        self._max = max(max_seconds, self._target)
        # A chunk is bounded by audio, but audio is not time. Someone talking
        # intermittently keeps the buffer open for minutes: on the 21:11
        # session one chunk held 17 seconds of speech spread over 482 seconds
        # of wall clock, so three utterances said eight minutes apart were
        # transcribed as one moment. Cap the span as well, and a chunk becomes
        # what was said in a stretch of conversation rather than a pile of
        # things said whenever.
        self._max_span = max_span_seconds
        self._pending: dict[int, _Pending] = {}
        self._next_seq = 1

    def add(
        self, user_id: int, pcm: bytes, started_at: float = 0.0
    ) -> Chunk | None:
        """Append one utterance; return a chunk when the buffer is full."""
        seconds = len(pcm) / (
            audio_cfg.output_sample_rate * audio_cfg.sample_width
        )
        state = self._pending.get(user_id)
        finished: Chunk | None = None
        if state is None:
            state = self._pending[user_id] = _Pending(
                seq=self._next_seq, started_at=started_at
            )
        elif (
            self._max_span > 0
            and started_at
            and state.started_at
            and started_at - state.started_at >= self._max_span
        ):
            # This utterance is too far from the ones already buffered to belong
            # with them, so it opens the next chunk instead of joining this one.
            # Appending first and flushing after would let the gap that tripped
            # the check sit inside the chunk it was meant to exclude.
            logger.info(
                "Chunk reached its %.0fs span on %d utterance(s) before it was "
                "full (%.0fs of audio).",
                self._max_span,
                state.utterances,
                state.seconds,
            )
            finished = self._take(user_id)
            state = self._pending[user_id] = _Pending(
                seq=self._next_seq, started_at=started_at
            )

        if state.utterances:
            state.parts.append(_silence(stt_cfg.chunk_gap_seconds))
            state.seconds += stt_cfg.chunk_gap_seconds
        state.parts.append(pcm)
        state.seconds += seconds
        state.utterances += 1

        # A chunk that was just opened holds one utterance, and the VAD never
        # emits one long enough to fill a chunk on its own, so the two cannot
        # both fire.
        if finished is None and state.seconds >= self._target:
            return self._take(user_id)
        return finished

    def flush(self) -> list[Chunk]:
        """Everything still buffered, for a stop or a disconnect."""
        return [self._take(uid) for uid in list(self._pending) if self._pending.get(uid)]

    def flush_user(self, user_id: int) -> Chunk | None:
        return self._take(user_id)

    def _take(self, user_id: int) -> Chunk | None:
        state = self._pending.get(user_id)
        if state is None or not state.utterances:
            return None
        self._pending.pop(user_id, None)
        self._next_seq += 1
        logger.info(
            "Built a %.0fs chunk from %d utterance(s) for user %s.",
            state.seconds,
            state.utterances,
            user_id,
        )
        return Chunk(
            user_id=user_id,
            seq=state.seq,
            pcm=b"".join(state.parts),
            seconds=state.seconds,
            utterances=state.utterances,
            started_at=state.started_at,
        )


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw PCM in the container the API expects."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(audio_cfg.output_channels)
        handle.setsampwidth(audio_cfg.sample_width)
        handle.setframerate(audio_cfg.output_sample_rate)
        handle.writeframes(pcm)
    return buf.getvalue()
