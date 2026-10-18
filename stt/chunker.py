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


@dataclass
class _Pending:
    seq: int = 0
    parts: list[bytes] = field(default_factory=list)
    seconds: float = 0.0
    utterances: int = 0


def _silence(seconds: float) -> bytes:
    frames = int(seconds * audio_cfg.output_sample_rate) * audio_cfg.sample_width
    return b"\x00" * max(frames, 0)


class AudioChunker:
    """Accumulate one speaker's utterances until there is enough to send.

    add() returns a Chunk the moment the buffer is full, so transcription starts
    while the session is still running: a two-hour game is twenty-four chunks
    settled as they arrive, not one wait at the end.
    """

    def __init__(self, target_seconds: float, max_seconds: float) -> None:
        self._target = max(target_seconds, 10.0)
        self._max = max(max_seconds, self._target)
        self._pending: dict[int, _Pending] = {}
        self._next_seq = 1

    def add(self, user_id: int, pcm: bytes) -> Chunk | None:
        """Append one utterance; return a chunk when the buffer is full."""
        seconds = len(pcm) / (
            audio_cfg.output_sample_rate * audio_cfg.sample_width
        )
        state = self._pending.setdefault(user_id, _Pending(seq=self._next_seq))
        if state.utterances:
            state.parts.append(_silence(stt_cfg.chunk_gap_seconds))
            state.seconds += stt_cfg.chunk_gap_seconds
        state.parts.append(pcm)
        state.seconds += seconds
        state.utterances += 1
        if state.seconds >= self._target:
            return self._take(user_id)
        return None

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
