"""Chunks: one speaker, several minutes, sent as soon as they are full."""

from config import audio_cfg, stt_cfg
from stt.chunker import AudioChunker

BYTES_PER_SEC = audio_cfg.output_sample_rate * audio_cfg.sample_width


def _pcm(seconds):
    return b"\x01\x02" * int(seconds * audio_cfg.output_sample_rate)


def test_nothing_is_emitted_until_the_chunk_is_full():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    assert chunker.add(1, _pcm(100)) is None
    assert chunker.add(1, _pcm(100)) is None
    assert chunker.add(1, _pcm(50)) is None
    chunk = chunker.add(1, _pcm(60))
    assert chunk is not None, "the fourth utterance crosses 300s"


def test_speakers_are_kept_apart():
    """Concatenating two people would lose who said what."""
    chunker = AudioChunker(target_seconds=30, max_seconds=60)
    assert chunker.add(1, _pcm(20)) is None
    assert chunker.add(2, _pcm(20)) is None
    chunk = chunker.add(1, _pcm(20))
    assert chunk is not None, "speaker 1 crosses 30s first"
    assert chunk.user_id == 1
    assert chunk.utterances == 2, "only speaker 1's two utterances, not speaker 2's"


def test_a_chunk_records_its_own_length_and_count():
    chunker = AudioChunker(target_seconds=10, max_seconds=20)
    chunker.add(1, _pcm(6))
    chunk = chunker.add(1, _pcm(6))
    assert chunk.utterances == 2
    assert chunk.seconds == 12 + stt_cfg.chunk_gap_seconds


def test_flush_returns_what_is_still_buffered():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    chunker.add(1, _pcm(20))
    chunker.add(2, _pcm(30))
    flushed = chunker.flush()
    assert sorted(c.user_id for c in flushed) == [1, 2]
    assert chunker.flush() == [], "and nothing twice"


def test_an_empty_flush_is_empty():
    assert AudioChunker(300, 450).flush() == []
