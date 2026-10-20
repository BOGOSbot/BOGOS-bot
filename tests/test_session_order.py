"""A session reads in the order it was spoken, not the order it was built.

Chunks are per speaker, and each speaker's buffer fills at their own rate, so
the quiet player's last five minutes can be transcribed before the talkative
one's first. Grouped by speaker is how the pipeline produces them; a
conversation is how they were said.
"""

from bot.client import STTBot, in_arrival_order
from stt.chunker import AudioChunker
from stt.gemini_transcriber import GeminiTranscriber, timed_segments, utterance_seconds

from tests.test_chunk_completeness import _bare_processor, _pcm

# Long enough to be cut, and varied, so the runaway check has nothing to say.
SPEECH = " ".join("Phrase %d qui compte plusieurs mots de plus." % i for i in range(12))


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


# -- a chunk that spans two turns ---------------------------------------------
# The chunker's buffer is per speaker and closes on 60s of audio or a 90s span,
# so a speaker whose turn ends and starts again while someone answers in between
# gets one chunk holding both turns. Dating that chunk by its first utterance is
# what put A's second turn before B's answer: measured on the 22:31 session, 99
# of its 199 seconds of speech provably sat before speech that came later.


def _resumed_turn():
    """A speaks, B answers, A carries on: two chunks, and one of them spans it."""
    chunker = AudioChunker(target_seconds=300, max_seconds=450, max_span_seconds=90)
    chunker.add(1, _pcm(10), started_at=1000.0)
    chunker.add(2, _pcm(2), started_at=1010.0)
    chunker.add(1, _pcm(10), started_at=1020.0)
    chunks = {chunk.user_id: chunk for chunk in chunker.flush()}
    return chunks[1], chunks[2]


def test_a_chunk_remembers_when_each_utterance_arrived():
    chunk = _resumed_turn()[0]
    assert chunk.started_at == 1000.0
    assert chunk.utterance_starts == (1000.0, 1020.0)


def test_a_chunk_remembers_where_someone_else_cut_in():
    """The turn boundary is the whole point: it is where the chunk must split."""
    chunk = _resumed_turn()[0]
    assert chunk.turn_breaks == (1,), "B answered between A's two utterances"
    assert _resumed_turn()[1].turn_breaks == ()


def test_a_turn_that_resumes_inside_one_chunk_is_dated_twice():
    chunk = _resumed_turn()[0]
    segments = timed_segments(
        SPEECH, utterance_seconds(chunk.pcm), chunk.utterance_starts, chunk.turn_breaks
    )
    assert len(segments) == 2, "one chunk, two turns"
    assert segments[0][0] == 1000.0
    assert segments[-1][0] == 1020.0, "the second turn keeps its own time"


def test_every_turn_inside_one_chunk_keeps_its_own_time():
    """A, B, A, C, A: three turns in one buffer, and three times to place them."""
    chunker = AudioChunker(target_seconds=300, max_seconds=450, max_span_seconds=90)
    chunker.add(1, _pcm(6), started_at=1000.0)
    chunker.add(2, _pcm(2), started_at=1010.0)
    chunker.add(1, _pcm(6), started_at=1020.0)
    chunker.add(3, _pcm(2), started_at=1030.0)
    chunker.add(1, _pcm(6), started_at=1040.0)
    chunk = {c.user_id: c for c in chunker.flush()}[1]

    assert chunk.utterance_starts == (1000.0, 1020.0, 1040.0)
    assert chunk.turn_breaks == (1, 2)
    segments = timed_segments(
        SPEECH, utterance_seconds(chunk.pcm), chunk.utterance_starts, chunk.turn_breaks
    )
    assert [at for at, _body in segments] == [1000.0, 1020.0, 1040.0]


def test_the_session_reads_as_a_conversation_not_as_blocks():
    """Without the split this is A then B, and A's second turn is lost in A's first."""
    entries = []
    for chunk in _resumed_turn():
        result = {
            "text": SPEECH,
            "started_at": chunk.started_at,
            "segments": timed_segments(
                SPEECH, utterance_seconds(chunk.pcm), chunk.utterance_starts
            ),
        }
        for at, _body in STTBot._blocks_of(result, SPEECH):
            entries.append(_entry("A" if chunk.user_id == 1 else "B", at))
    assert [e["name"] for e in in_arrival_order(entries)] == ["A", "B", "A"]


def test_the_transcriber_dates_its_own_reading():
    chunk = _resumed_turn()[0]
    transcriber = GeminiTranscriber(model="test/model")
    transcriber._ask = lambda prompt, pcm: {
        "text": SPEECH,
        "latency": 0.1,
        "cost": 0.0,
        "durations": utterance_seconds(pcm),
    }
    result = transcriber.transcribe(1, chunk.pcm, starts=chunk.utterance_starts)
    assert [at for at, _ in result["segments"]] == [1000.0, 1020.0]


# -- when there is no timeline to trust ---------------------------------------


def test_a_timeline_that_starts_at_zero_is_not_trusted():
    """A chunker that never learned when anything arrived dates nothing."""
    assert timed_segments("Une. Deux.", [1.0, 1.0], (0.0, 1.0)) == []


def test_a_timeline_of_the_wrong_length_is_not_trusted():
    assert timed_segments("Une. Deux.", [1.0, 1.0], (5.0,)) == []


def test_a_reading_with_no_timeline_is_left_whole():
    result = {"text": "Bonjour.", "started_at": 42.0}
    assert STTBot._blocks_of(result, "Bonjour.") == [(42.0, "Bonjour.")]


def test_a_dated_reading_is_split_by_time():
    result = {"segments": [(1000.0, "Une."), (1020.0, "Deux.")]}
    assert STTBot._blocks_of(result, "Une. Deux.") == [
        (1000.0, "Une."),
        (1020.0, "Deux."),
    ]


def test_the_worker_hands_the_timeline_to_the_transcriber():
    """The queue item grew a field; only a run exercises its arity."""
    processor = _bare_processor([])
    seen = {}

    class _Stub:
        def transcribe(self, user_id, audio, starts=(), turn_breaks=()):
            seen["starts"] = starts
            seen["breaks"] = turn_breaks
            return None

    processor._transcriber = _Stub()
    processor._deliver = lambda seq, result: None
    processor._utterances.put(
        (1, 7, _pcm(2), 2.0, 1000.0, ((1000.0, 1001.0), (1,)))
    )
    processor._utterances.put(None)
    processor._transcribe_loop()
    assert seen["starts"] == (1000.0, 1001.0)
    assert seen["breaks"] == (1,)
