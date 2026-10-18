"""A chunk that comes back too thin to be its own audio is not the transcript.

Measured on the 04:30 session: 24.4s of speech came back as "Ok." and the file
was posted as if complete. The reading is checked against the audio length
here, and re-asked when it cannot be right.
"""

import queue
import threading

import pytest

from config import audio_cfg, stt_cfg
from stt.chunker import AudioChunker
from stt.gemini_transcriber import (
    GeminiTranscriber,
    _halves,
    looks_complete,
    tidy,
    utterance_seconds,
)
from stt.processor import STTProcessor
from stt.user_state import UserStateManager

BYTES_PER_SEC = audio_cfg.output_sample_rate * audio_cfg.sample_width


def _pcm(seconds, level=513):
    return level.to_bytes(2, "little", signed=True) * int(
        seconds * audio_cfg.output_sample_rate
    )


# -- the completeness check ---------------------------------------------------


def test_one_sentence_is_not_a_reading_of_twenty_four_seconds():
    assert not looks_complete("Ok.", 24.4)


def test_a_full_reading_passes():
    assert looks_complete(" ".join(["mot"] * 61), 24.4)


def test_a_clock_is_not_a_transcript():
    """A 300s chunk came back as 00:00 through 04:59 and nothing else."""
    clock = " ".join("%02d:%02d" % divmod(i, 60) for i in range(300))
    assert not looks_complete(clock, 300.0)


def test_an_empty_answer_is_not_complete():
    assert not looks_complete("", 10.0)


def test_a_short_chunk_is_held_to_a_short_floor():
    """Three words really can be all there was in a two-second chunk."""
    assert looks_complete("Oui, c'est bon.", 2.0)


def test_one_word_is_a_fair_reading_of_a_two_second_clip():
    """Measured: a 1.8s chunk said "Salut." and the rate flagged it at 0.5 w/s."""
    assert looks_complete("Salut.", 1.8)


def test_a_long_chunk_is_still_held_to_the_rate():
    assert not looks_complete("Ok.", 24.4)


# -- the paragraphing --------------------------------------------------------


def test_a_wall_of_single_newlines_becomes_paragraphs():
    wall = "Une.\nDeux.\nTrois.\nQuatre."
    assert tidy(wall) == "Une.\n\nDeux.\n\nTrois.\n\nQuatre."


def test_existing_paragraphs_are_left_alone():
    body = "Une.\n\nDeux.\n\nTrois."
    assert tidy(body) == body


def test_a_wall_becomes_blocks_instead_of_a_slab():
    """Five-minute chunks came back as one 617-word paragraph."""
    wall = " ".join(["Mot un. Mot deux. Mot trois. Mot quatre."] * 15)
    out = tidy(wall)
    assert "\n\n" in out, "a wall is not a paragraph"
    assert max(len(b.split()) for b in out.split("\n\n")) <= 100


def test_a_decimal_does_not_end_a_sentence():
    assert tidy("Il fait 3.7 puis 5.2 et c'est tout.") == "Il fait 3.7 puis 5.2 et c'est tout."


def test_a_wall_breaks_where_the_speaker_paused():
    """The chunker glued the utterances with silence; the boundaries are still
    in the PCM, so the model's slab can be cut along them."""
    text = " ".join(["Mot un. Mot deux. Mot trois."] * 20)   # 120 words, no breaks
    durations = [10.0] * 12                                  # twelve utterances
    out = tidy(text, durations)
    assert out.count("\n\n") >= 5, "twelve utterances is not one paragraph"
    assert " ".join(out.split()) == " ".join(text.split()), "and no word moved"


def test_a_wall_with_no_timeline_still_gets_blocks():
    text = " ".join(["Mot un. Mot deux. Mot trois."] * 20)
    assert "\n\n" in tidy(text, [])


def test_utterance_seconds_reads_the_chunker_gaps():
    gap = b"\x00\x00" * int(0.4 * audio_cfg.output_sample_rate)
    pcm = _pcm(3) + gap + _pcm(5) + gap + _pcm(2)
    durations = utterance_seconds(pcm)
    assert len(durations) == 3
    assert durations[0] == pytest.approx(3.0, abs=0.05)
    assert durations[1] == pytest.approx(5.0, abs=0.05)


# -- the retry ----------------------------------------------------------------


def test_a_thin_answer_is_re_asked_and_the_longer_one_wins():
    transcriber = GeminiTranscriber(model="test/model")
    calls = []

    def fake_ask(prompt, pcm):
        calls.append(prompt)
        text = "Ok." if len(calls) == 1 else " ".join(["mot"] * 40)
        return {"text": text, "latency": 0.1, "cost": 0.0}

    transcriber._ask = fake_ask
    result = transcriber.transcribe(1, _pcm(24.4))
    assert len(calls) == 2, "the first reading was too thin to accept"
    assert len(result["text"].split()) == 40
    assert result["attempts"] == 2
    assert "first word to the last" in calls[1], "the retry asks differently"


def test_a_good_first_answer_is_not_re_asked():
    transcriber = GeminiTranscriber(model="test/model")
    calls = []

    def fake_ask(prompt, pcm):
        calls.append(prompt)
        return {"text": " ".join(["mot"] * 60), "latency": 0.1, "cost": 0.0}

    transcriber._ask = fake_ask
    result = transcriber.transcribe(1, _pcm(24.4))
    assert len(calls) == 1
    assert result["attempts"] == 1


def test_the_longest_reading_is_kept_when_neither_attempt_is_complete():
    """Below the split floor there is nothing left to try, so keep the best."""
    transcriber = GeminiTranscriber(model="test/model")
    answers = iter(["Ok.", "Ok, je vais faire ca."])
    transcriber._ask = lambda prompt, pcm: {
        "text": next(answers), "latency": 0.1, "cost": 0.0,
    }
    result = transcriber.transcribe(1, _pcm(15))
    assert result["text"] == "Ok, je vais faire ca."


# -- the split ----------------------------------------------------------------


def _split_aware_ask(calls, whole_limit=40):
    """Abandon anything longer than *whole_limit* seconds, answer the rest."""

    def ask(prompt, pcm):
        calls.append(pcm)
        seconds = len(pcm) / BYTES_PER_SEC
        if seconds > whole_limit:
            return {"text": "Ok.", "latency": 0.1, "cost": 0.0}
        return {"text": " ".join(["mot"] * 40), "latency": 0.1, "cost": 0.0}

    return ask


def test_an_abandoned_chunk_is_recovered_by_asking_about_its_halves():
    """The same audio asked the same way stops in the same place.

    Measured: a 74.5s chunk came back as 18 words twice over, and the same
    audio in four pieces gave 180 words, including the passage that was
    missing. Halving is a different question, and the model answers it.
    """
    transcriber = GeminiTranscriber(model="test/model")
    calls = []
    transcriber._ask = _split_aware_ask(calls)
    result = transcriber.transcribe(1, _pcm(60))
    assert result.get("split") is True
    assert len(result["text"].split()) == 80, "two halves, forty words each"
    assert len(calls) == 4, "two attempts at the whole, then one per half"


def test_a_split_is_not_attempted_below_the_floor():
    transcriber = GeminiTranscriber(model="test/model")
    calls = []
    transcriber._ask = _split_aware_ask(calls, whole_limit=1)
    transcriber.transcribe(1, _pcm(15))
    assert len(calls) == 2, "two attempts, no split: a fragment invites invention"


def test_halves_split_at_silence_rather_than_through_a_word():
    speech = _pcm(10)
    silence = b"\x00\x00" * int(0.5 * audio_cfg.output_sample_rate)
    left, right = _halves(speech + silence + _pcm(10))
    assert len(speech) <= len(left) <= len(speech) + len(silence)


def test_halves_fall_back_to_the_middle_when_there_is_no_silence():
    left, right = _halves(_pcm(10))
    assert len(left) == len(right)


# -- the flush ---------------------------------------------------------------


class _AlwaysSpeech:
    """A VAD that calls everything speech, so the test does not need Silero."""

    def frame_to_tensor(self, frame):
        return frame

    def create_iterator(self):
        return _Speech()


class _Speech:
    triggered = True

    def __call__(self, tensor, return_seconds=False):
        return None


def _bare_processor(audio_items):
    """An STTProcessor with its queues and stub VAD, but no models loaded."""
    processor = object.__new__(STTProcessor)
    processor._audio_q = queue.Queue()
    for item in audio_items:
        processor._audio_q.put(item)
    processor._vad = _AlwaysSpeech()
    processor._users = UserStateManager(
        vad_iterator_factory=processor._vad.create_iterator
    )
    processor._chunker = AudioChunker(stt_cfg.chunk_seconds, stt_cfg.chunk_max_seconds)
    processor._utterances = queue.Queue()
    processor._names = {}
    processor._warned_at = 0
    processor._seq = 0
    processor._seq_lock = threading.Lock()
    processor._dropped_quiet = 0
    processor._dropped_short = 0
    processor._dump_n = 0
    return processor


def test_audio_still_queued_at_flush_time_is_transcribed():
    """FLUSH_ALL arrives at the top of the loop, with audio still behind it.

    Flushing first would leave those frames to be VAD'd into a chunker that
    had already been emptied, and they would never reach the model.
    """
    processor = _bare_processor([(7, "Mandal", _pcm(1.0))])
    processor._flush_everything("flush-all")
    assert processor._utterances.qsize() == 1, "the queued speech became a chunk"


def test_flush_with_nothing_queued_is_a_no_op():
    processor = _bare_processor([])
    processor._flush_everything("flush-all")
    assert processor._utterances.qsize() == 0
