"""A session reads in the order it was spoken, and every line says when.

A chunk is one turn, so it is emitted when the next speaker starts: the order
the chunks come out of the pipeline is already the order of the conversation.
Each still carries the wall-clock time of its first utterance, because a worker
finishes when it finishes, and because the line prints that time.
"""

from datetime import datetime

from bot.client import _clock, in_arrival_order
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


# -- the stamp on the line ----------------------------------------------------


def test_every_line_says_when_it_started():
    """No guesswork to audit: the reader can see the order the bot used."""
    at = datetime(2026, 10, 20, 22, 51, 7).timestamp()
    assert _clock(at) == "22:51:07"


def test_a_line_with_no_clock_prints_no_stamp():
    """A zero would read as midnight, which is a claim the worker did not make."""
    assert _clock(0.0) == ""
    assert _clock(None) == ""
    assert _clock("not a time") == ""


def test_the_turns_come_out_in_the_order_they_were_said():
    """One turn each, emitted as the next speaker starts, so the order is exact."""
    chunker = AudioChunker(target_seconds=300, max_seconds=450)
    said = [(1, 1000.0), (2, 1010.0), (1, 1020.0), (3, 1030.0), (2, 1040.0)]
    emitted = []
    for user_id, at in said:
        emitted += chunker.add(user_id, _pcm(4), started_at=at)
    emitted += chunker.flush()

    assert [(c.user_id, c.started_at) for c in emitted] == said
    assert [e["name"] for e in in_arrival_order(
        [_entry(str(c.user_id), c.started_at) for c in emitted]
    )] == ["1", "2", "1", "3", "2"]
