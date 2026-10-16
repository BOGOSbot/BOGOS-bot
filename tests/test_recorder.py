"""The channel recorder: one WAV per speaker, never a risk to the audio path."""

from __future__ import annotations

import json
import wave

from bot.recorder import ChannelRecorder


def _frames(path) -> int:
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16_000
        return handle.getnframes()


def test_one_wav_per_speaker_with_both_chunks_appended(tmp_path) -> None:
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(1, "Nova (Alex)", b"\x00\x01" * 100)
    recorder.write(2, "Zed (Sam)", b"\x02\x03" * 50)
    recorder.write(1, "Nova (Alex)", b"\x04\x05" * 100)
    recorder.close()

    assert len(list(tmp_path.glob("*.wav"))) == 2
    first = next(p for p in tmp_path.glob("*.wav") if "__1__" in p.name)
    assert _frames(first) == 200  # both chunks, one continuous file


def test_a_session_index_names_the_speakers(tmp_path) -> None:
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(1, "Nova (Alex)", b"\x00\x00" * 10)
    recorder.close()

    index = list(tmp_path.glob("*__session.json"))
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


def test_close_is_idempotent(tmp_path) -> None:
    recorder = ChannelRecorder(str(tmp_path))
    recorder.write(1, "anyone", b"\x00\x00")
    recorder.close()
    recorder.close()
    assert len(list(tmp_path.glob("*__session.json"))) == 1
