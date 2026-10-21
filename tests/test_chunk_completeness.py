"""A chunk that comes back too thin to be its own audio is not the transcript.

Measured on the 04:30 session: 24.4s of speech came back as "Ok." and the file
was posted as if complete. The reading is checked against the audio length
here, and re-asked when it cannot be right.
"""

import json
import queue
import threading
import urllib.request
import wave
from types import SimpleNamespace
from datetime import datetime
from pathlib import Path

import pytest

import recording
from config import audio_cfg, stt_cfg
from stt.chunker import AudioChunker
from stt import glossary
import stt.chunk_transcriber as chunk_transcriber
from stt.chunk_transcriber import (
    GeminiTranscriber,
    VoxtralTranscriber,
    build_transcriber,
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


def _words(count):
    """Plausible speech for a length: distinct words, because no real reading is
    one word repeated and the runaway check now says so."""
    return " ".join("mot%d" % i for i in range(count))


# -- the completeness check ---------------------------------------------------


def test_one_sentence_is_not_a_reading_of_twenty_four_seconds():
    assert not looks_complete("Ok.", 24.4)


def test_a_full_reading_passes():
    assert looks_complete(_words(61), 24.4)


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


# -- the runaway --------------------------------------------------------------
# The opposite failure, measured on this bot's own recordings in the rules
# repository's voice benchmark: a 99s clip was answered to the 4,096-token cap
# with "Hmm." over and over, $0.010 for the one call. The reading is longer than
# the audio can hold, and it is not a reading of anything.


def test_a_loop_is_not_a_reading():
    assert not looks_complete(" ".join(["Hmm."] * 200), 99.0)


def test_a_loop_that_never_produced_a_full_stop_is_not_a_reading():
    assert not looks_complete(" ".join(["Hmm"] * 200), 99.0)


def test_a_truncated_answer_is_not_a_reading():
    """finish_reason "length" is the model saying the rest is not in there."""
    assert not looks_complete(_words(61), 24.4, truncated=True)


def test_repeating_oui_a_few_times_is_still_a_reading():
    """Table talk repeats itself. Six of them is not a loop."""
    assert looks_complete("Oui. " * 5 + _words(50), 24.4)


def test_a_long_runaway_does_not_win_on_length():
    """It is longer than the truth, and length used to be the only tie-break."""
    transcriber = GeminiTranscriber(model="test/model")
    answers = iter([" ".join(["Hmm."] * 300), _words(50)])
    transcriber._ask = lambda pcm, attempt: {
        "text": next(answers), "latency": 0.1, "cost": 0.0,
    }
    result = transcriber.transcribe(1, _pcm(24.4))
    assert result["text"] == _words(50)
    assert result["attempts"] == 2


def test_a_truncated_reading_is_re_asked():
    transcriber = GeminiTranscriber(model="test/model")
    calls = []
    answers = iter([
        {"text": _words(50), "latency": 0.1, "cost": 0.0, "truncated": True},
        {"text": _words(50), "latency": 0.1, "cost": 0.0, "truncated": False},
    ])

    def fake_ask(pcm, attempt):
        calls.append(attempt)
        return next(answers)

    transcriber._ask = fake_ask
    result = transcriber.transcribe(1, _pcm(24.4))
    assert len(calls) == 2, "a truncated reading is not a full one"
    assert result.get("truncated") is False


def test_the_retry_prompt_says_what_to_do(monkeypatch):
    """The first prompt says what not to do; the early stop ignores that."""
    seen = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _capture(seen, _words(50), "stop")
    )
    GeminiTranscriber(model="test/model")._ask(_pcm(24.4), 2)
    prompt = seen["body"]["messages"][0]["content"][0]["text"]
    assert "first word to the last" in prompt, "the second ask is worded differently"


@pytest.fixture
def a_glossary(tmp_path, monkeypatch):
    """A shipped list of our own, so the prompt has a term to carry."""
    path = tmp_path / "glossary.txt"
    path.write_text("Aeldari\nDrukhari\n", encoding="utf-8")
    monkeypatch.setattr(
        glossary,
        "stt_cfg",
        SimpleNamespace(glossary_file=str(path), glossary_local_file=""),
    )
    monkeypatch.setattr(glossary, "session_path", lambda: tmp_path / "session.txt")
    return path


def test_the_vocabulary_travels_on_both_attempts(a_glossary, monkeypatch):
    """The retry keeps the terms, so a re-ask cannot lose the names."""
    seen = {}
    monkeypatch.setattr(
        urllib.request, "urlopen", _capture(seen, _words(50), "stop")
    )
    GeminiTranscriber(model="test/model")._ask(_pcm(24.4), 1)
    prompt = seen["body"]["messages"][0]["content"][0]["text"]
    assert "Spell these names" in prompt


def _capture(seen, text, finish):
    """A urlopen that records the request and answers with one reading."""

    class _Response:
        def __init__(self):
            self._payload = json.dumps(
                {
                    "choices": [{"message": {"content": text}, "finish_reason": finish}],
                    "usage": {"cost": 0.0},
                }
            ).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    return fake_urlopen


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_the_token_cap_is_read_off_the_answer(monkeypatch):
    """The model reports the cap itself; nothing here has to guess."""
    payload = {
        "choices": [{"message": {"content": _words(50)}, "finish_reason": "length"}],
        "usage": {"cost": 0.01},
    }
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: _FakeResponse(payload)
    )
    result = GeminiTranscriber(model="test/model")._ask(_pcm(24.4), 1)
    assert result["truncated"] is True


# -- the second route --------------------------------------------------------
# Voxtral Transcribe takes audio and returns text, with no prompt, so the
# vocabulary arrives as context_bias. The field must be a comma-separated
# string: the same terms as a JSON array are accepted and then ignored.


def _voxtral_capture(seen, text="Bonjour.", cost=0.003):
    class _Response:
        def __init__(self):
            self._payload = json.dumps(
                {"text": text, "usage": {"cost": cost, "seconds": 24}}
            ).encode("utf-8")

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return _Response()

    return fake_urlopen


def test_the_voxtral_route_posts_to_the_transcription_endpoint(
    a_glossary, monkeypatch
):
    seen = {}
    monkeypatch.setattr(urllib.request, "urlopen", _voxtral_capture(seen))
    result = VoxtralTranscriber(model="test/model")._ask(_pcm(24.4), 1)

    assert seen["url"].endswith("/audio/transcriptions")
    assert seen["body"]["input_audio"]["format"] == "wav"
    assert seen["body"]["model"] == "test/model"
    assert result["text"] == "Bonjour."
    assert result["cost"] == 0.003


def test_the_vocabulary_is_a_string_and_not_a_list(a_glossary, monkeypatch):
    """A JSON array is accepted and then ignored, silently, by this endpoint."""
    seen = {}
    monkeypatch.setattr(urllib.request, "urlopen", _voxtral_capture(seen))
    VoxtralTranscriber(model="test/model")._ask(_pcm(24.4), 1)

    options = seen["body"]["provider"]["options"][stt_cfg.bias_provider]
    assert options["context_bias"] == "Aeldari, Drukhari"
    assert isinstance(options["context_bias"], str), (
        "a list here reads as no bias at all"
    )


def test_a_route_with_nothing_to_bias_sends_no_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(
        glossary,
        "stt_cfg",
        SimpleNamespace(
            glossary_file=str(tmp_path / "missing.txt"), glossary_local_file=""
        ),
    )
    monkeypatch.setattr(glossary, "session_path", lambda: tmp_path / "none.txt")
    seen = {}
    monkeypatch.setattr(urllib.request, "urlopen", _voxtral_capture(seen))
    VoxtralTranscriber(model="test/model")._ask(_pcm(24.4), 1)
    assert "provider" not in seen["body"]


def test_the_second_attempt_is_asked_at_a_different_temperature(
    a_glossary, monkeypatch
):
    """There is no prompt to reword, so the re-ask is the sampler nudged."""
    seen = {}
    monkeypatch.setattr(urllib.request, "urlopen", _voxtral_capture(seen))
    transcriber = VoxtralTranscriber(model="test/model")
    transcriber._ask(_pcm(24.4), 1)
    first = seen["body"].get("temperature")
    transcriber._ask(_pcm(24.4), 2)
    assert seen["body"]["temperature"] != first


def test_the_configured_route_is_the_one_built(monkeypatch):
    """The widget flips STT_CHUNK_PROVIDER; nothing else has to change."""
    monkeypatch.setattr(
        chunk_transcriber,
        "stt_cfg",
        SimpleNamespace(
            active_chunk_provider="gemini",
            chunk_provider="gemini",
            chunk_model="v",
            chunk_model_gemini="g",
        ),
    )
    assert isinstance(build_transcriber(), GeminiTranscriber)

    monkeypatch.setattr(
        chunk_transcriber,
        "stt_cfg",
        SimpleNamespace(
            active_chunk_provider="voxtral",
            chunk_provider="voxtral",
            chunk_model="v",
            chunk_model_gemini="g",
        ),
    )
    assert isinstance(build_transcriber(), VoxtralTranscriber)


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

    def fake_ask(pcm, attempt):
        calls.append(attempt)
        text = "Ok." if len(calls) == 1 else _words(40)
        return {"text": text, "latency": 0.1, "cost": 0.0}

    transcriber._ask = fake_ask
    result = transcriber.transcribe(1, _pcm(24.4))
    assert len(calls) == 2, "the first reading was too thin to accept"
    assert len(result["text"].split()) == 40
    assert result["attempts"] == 2
    assert calls == [1, 2], "the second ask is a different question"


def test_a_good_first_answer_is_not_re_asked():
    transcriber = GeminiTranscriber(model="test/model")
    calls = []

    def fake_ask(pcm, attempt):
        calls.append(attempt)
        return {"text": _words(60), "latency": 0.1, "cost": 0.0}

    transcriber._ask = fake_ask
    result = transcriber.transcribe(1, _pcm(24.4))
    assert len(calls) == 1
    assert result["attempts"] == 1


def test_the_longest_reading_is_kept_when_neither_attempt_is_complete():
    """Below the split floor there is nothing left to try, so keep the best."""
    transcriber = GeminiTranscriber(model="test/model")
    answers = iter(["Ok.", "Ok, je vais faire ca."])
    transcriber._ask = lambda pcm, attempt: {
        "text": next(answers), "latency": 0.1, "cost": 0.0,
    }
    result = transcriber.transcribe(1, _pcm(15))
    assert result["text"] == "Ok, je vais faire ca."


# -- the split ----------------------------------------------------------------


def _split_aware_ask(calls, whole_limit=40):
    """Abandon anything longer than *whole_limit* seconds, answer the rest."""

    def ask(pcm, attempt):
        calls.append(pcm)
        seconds = len(pcm) / BYTES_PER_SEC
        if seconds > whole_limit:
            return {"text": "Ok.", "latency": 0.1, "cost": 0.0}
        return {"text": _words(40), "latency": 0.1, "cost": 0.0}

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


# -- where an utterance is filed ---------------------------------------------
# The processor harness lives in this file, so the two tests that need one live
# here too. What they pin belongs to recording.py: a dump lands in the running
# conversation's folder, and nowhere at all when no conversation is open.


@pytest.fixture
def isolated_recording(tmp_path, monkeypatch):
    """No developer recording.json, no environment, a session pointer of our own."""
    monkeypatch.setattr(recording, "settings_path", lambda: tmp_path / "recording.json")
    monkeypatch.setattr(recording, "session_path", lambda: tmp_path / "live-session.json")
    monkeypatch.delenv("RECORD_DIR", raising=False)
    monkeypatch.delenv("STT_DUMP_DIR", raising=False)
    return tmp_path


def test_a_dump_lands_in_the_running_conversations_folder(isolated_recording):
    root = isolated_recording / "recordings"
    session = recording.open_session(
        recording.Choice(True, str(root), str(root), str(root)),
        started=datetime(2026, 10, 17, 21, 11, 9),
    )
    processor = _bare_processor([])
    processor._dump(7, b"\x01\x02" * 100)

    written = list(Path(session.dump_dir).glob("*.wav"))
    assert len(written) == 1
    with wave.open(str(written[0]), "rb") as handle:
        assert handle.getnframes() == 100


def test_a_dump_with_no_conversation_open_writes_nothing(isolated_recording):
    """A bare processor must not fall back on the developer's own recordings."""
    processor = _bare_processor([])
    processor._dump(7, b"\x01\x02" * 100)

    assert list(isolated_recording.rglob("*.wav")) == []
