"""The channel recorder: one WAV per speaker, never a risk to the audio path."""

from __future__ import annotations

import json
import wave
from datetime import datetime

from bot.recorder import ChannelRecorder


def _frames(path) -> int:
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        return handle.getnframes()


def test_one_wav_per_speaker_with_both_chunks_appended(tmp_path) -> None:
    # The folder is the session now, so the filenames carry no timestamp.
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(1, "Nova (Alex)", b"\x00\x01" * 100)
    recorder.write(2, "Zed (Sam)", b"\x02\x03" * 50)
    recorder.write(1, "Nova (Alex)", b"\x04\x05" * 100)
    recorder.close()

    assert len(list(tmp_path.glob("*.wav"))) == 2
    first = next(p for p in tmp_path.glob("*.wav") if p.name.startswith("1__"))
    assert _frames(first) == 200  # both chunks, one continuous file


def test_a_session_index_names_the_speakers(tmp_path) -> None:
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(1, "Nova (Alex)", b"\x00\x00" * 10)
    recorder.close()

    index = list(tmp_path.glob("session.json"))
    assert len(index) == 1
    payload = json.loads(index[0].read_text())
    assert payload["speakers"] == {"1": "Nova (Alex)"}
    assert payload["sample_rate"] == 16_000


def test_a_display_name_is_made_filename_safe(tmp_path) -> None:
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(7, "Nova/38 (Alex)*", b"\x00\x00")
    recorder.close()

    name = next(tmp_path.glob("*.wav")).name
    assert "/" not in name and "(" not in name and "*" not in name


def test_disabled_without_a_directory(tmp_path) -> None:
    recorder = ChannelRecorder("")
    assert recorder.enabled is False
    recorder.write(1, "anyone", b"\x00\x00")  # must not raise
    recorder.close()
    assert list(tmp_path.iterdir()) == []


def test_a_directory_it_cannot_create_is_survivable(tmp_path) -> None:
    # Recording is optional; losing the transcript is not. A path that is a
    # file must degrade to silence, not to an exception on the audio path.
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    recorder = ChannelRecorder(str(blocker))
    recorder.write(1, "anyone", b"\x00\x00")
    recorder.close()
    assert blocker.read_text() == "x"


def test_the_index_records_the_start_time_it_was_given(tmp_path) -> None:
    """The folder is named for the session, so the index must agree with it."""
    started = datetime(2026, 10, 17, 21, 11, 9)
    recorder = ChannelRecorder(str(tmp_path), started=started)
    recorder.write(1, "Nova (Alex)", b"" * 10)
    recorder.close()

    payload = json.loads((tmp_path / "session.json").read_text())
    assert payload["started"].startswith("2026-10-17T21:11:09")


def test_close_is_idempotent(tmp_path) -> None:
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(1, "anyone", b"\x00\x00")
    recorder.close()
    recorder.close()
    assert len(list(tmp_path.glob("session.json"))) == 1


def test_the_index_is_utf8_so_an_accented_name_survives(tmp_path) -> None:
    """The default encoding on Windows is ANSI; the file must still be JSON."""
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(2, "Zoé (Amélie)", b"\x00\x00")
    recorder.close()

    raw = (tmp_path / "session.json").read_bytes()
    payload = json.loads(raw.decode("utf-8"))  # must not raise
    assert payload["speakers"]["2"] == "Zoé (Amélie)"
