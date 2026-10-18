"""A session reads in the order it was spoken, not the order it was built.

Chunks are per speaker, and each speaker's buffer fills at their own rate, so
the quiet player's last five minutes can be transcribed before the talkative
one's first. Grouped by speaker is how the pipeline produces them; a
conversation is how they were said.
"""

from bot.client import in_arrival_order
from stt.chunker import AudioChunker

from tests.test_chunk_completeness import _pcm


def _entry(name, started_at):
    return {"name": name, "started_at": started_at, "line": "**%s** : x" % name}


def test_chunks_come_back_in_the_order_they_were_spoken():
    entries = [_entry("b", 200.0), _entry("a", 100.0), _entry("c", 300.0)]
    assert [e["name"] for e in in_arrival_order(entries)] == ["a", "b", "c"]


def test_the_order_is_untouched_when_a_chunk_has_no_time():
    """Better grouped by speaker than ordered by a guess."""
    entries = [_entry("b", 200.0), {"name": "a"}, _entry("c", 300.0)]
    assert [e["name"] for e in in_arrival_order(entries)] == ["b", "a", "c"]


def test_an_empty_session_is_an_empty_session():
    assert in_arrival_order([]) == []


def test_a_chunk_remembers_when_its_first_utterance_arrived():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    chunker.add(1, _pcm(5), started_at=1000.0)
    chunker.add(1, _pcm(5), started_at=1005.0)
    chunk = chunker.flush()[0]
    assert chunk.started_at == 1000.0, "the first utterance, not the last"


def test_a_chunk_without_a_time_reports_zero():
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    chunker.add(1, _pcm(5))
    assert chunker.flush()[0].started_at == 0.0
