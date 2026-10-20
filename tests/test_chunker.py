"""Chunks: one turn of one speaker, sent as soon as the turn ends."""

from config import audio_cfg, stt_cfg
from stt.chunker import AudioChunker

BYTES_PER_SEC = audio_cfg.output_sample_rate * audio_cfg.sample_width


def _pcm(seconds):
    return b"\x01\x02" * int(seconds * audio_cfg.output_sample_rate)


def test_nothing_is_emitted_until_the_chunk_is_full():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    assert chunker.add(1, _pcm(100)) == []
    assert chunker.add(1, _pcm(100)) == []
    assert chunker.add(1, _pcm(50)) == []
    assert chunker.add(1, _pcm(60)), "the fourth utterance crosses 300s"


def test_a_turn_ends_when_someone_else_speaks():
    """The buffer closes on the answer, so a chunk is never two turns.

    A chunk holding two turns cannot be placed in time without guessing where
    the second one begins in the text, and that guess is what put sentences on
    the wrong side of an answer.
    """
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    assert chunker.add(1, _pcm(10), started_at=1000.0) == []
    closed = chunker.add(2, _pcm(3), started_at=1010.0)
    assert [chunk.user_id for chunk in closed] == [1]
    assert closed[0].started_at == 1000.0, "and it keeps the time it started at"


def test_a_turn_that_resumes_is_a_second_chunk():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    chunks = []
    chunks += chunker.add(1, _pcm(10), started_at=1000.0)
    chunks += chunker.add(2, _pcm(3), started_at=1010.0)
    chunks += chunker.add(1, _pcm(10), started_at=1020.0)
    chunks += chunker.flush()
    assert [(c.user_id, c.started_at) for c in chunks] == [
        (1, 1000.0),
        (2, 1010.0),
        (1, 1020.0),
    ], "one turn each, in the order they were said"


def test_speakers_are_kept_apart():
    """Concatenating two people would lose who said what."""
    chunker = AudioChunker(target_seconds=30, max_seconds=60)
    assert chunker.add(1, _pcm(20)) == []
    closed = chunker.add(2, _pcm(20))
    assert [c.user_id for c in closed] == [1]
    assert closed[0].utterances == 1, "speaker 1's own utterance, and no more"
    buffered = chunker.flush()
    assert [c.user_id for c in buffered] == [2]
    assert buffered[0].utterances == 1


def test_a_chunk_records_its_own_length_and_count():
    chunker = AudioChunker(target_seconds=10, max_seconds=20)
    assert chunker.add(1, _pcm(6)) == []
    closed = chunker.add(1, _pcm(6))
    assert len(closed) == 1
    assert closed[0].utterances == 2
    assert closed[0].seconds == 12 + stt_cfg.chunk_gap_seconds


def test_flush_returns_what_is_still_buffered():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    assert chunker.add(1, _pcm(20)) == []
    closed = chunker.add(2, _pcm(30))
    assert [c.user_id for c in closed] == [1], "speaker 2 closed speaker 1"
    flushed = chunker.flush()
    assert [c.user_id for c in flushed] == [2]
    assert chunker.flush() == [], "and nothing twice"


def test_an_empty_flush_is_empty():
    assert AudioChunker(300, 450).flush() == []


def test_a_chunk_is_cut_when_it_spans_too_much_time():
    """Measured: 17s of speech spread over 482s of wall clock, read as one moment.

    Someone talking intermittently keeps the buffer open for minutes. A chunk
    is bounded by audio, and audio is not time.
    """
    chunker = AudioChunker(target_seconds=60, max_seconds=450, max_span_seconds=90)
    assert chunker.add(1, _pcm(10), started_at=1000.0) == []
    assert chunker.add(1, _pcm(10), started_at=1030.0) == []
    closed = chunker.add(1, _pcm(10), started_at=1095.0)
    assert closed, "95 seconds is past the span"
    chunk = closed[0]
    assert chunk.seconds < 60, "and it was nowhere near full"
    assert chunk.utterances == 2, (
        "the utterance that tripped the span opens the next chunk; appending "
        "it first would put the very gap being excluded inside the chunk"
    )


def test_no_span_cap_leaves_the_chunk_alone():
    chunker = AudioChunker(target_seconds=60, max_seconds=450)
    chunker.add(1, _pcm(10), started_at=1000.0)
    assert chunker.add(1, _pcm(10), started_at=100000.0) == []
