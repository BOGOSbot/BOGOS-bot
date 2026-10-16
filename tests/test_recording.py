"""The recording switch: one file decides what reaches the disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import recording


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A recording.json of our own, and no inherited environment."""
    path = tmp_path / "recording.json"
    monkeypatch.setattr(recording, "settings_path", lambda: path)
    monkeypatch.delenv("RECORD_DIR", raising=False)
    monkeypatch.delenv("STT_DUMP_DIR", raising=False)
    recording.reset_cache()
    yield path
    recording.reset_cache()


def test_without_the_file_the_environment_still_decides(settings, monkeypatch):
    """An existing .env must keep behaving exactly as it did before."""
    monkeypatch.setenv("STT_DUMP_DIR", "dumps")
    choice = recording.current()
    assert choice.dump_dir == "dumps"
    assert choice.session_dir == ""


def test_off_overrides_the_environment(settings, monkeypatch):
    """The whole point of the switch: off beats a leftover .env value."""
    monkeypatch.setenv("RECORD_DIR", "E:/recordings")
    monkeypatch.setenv("STT_DUMP_DIR", "dumps")
    recording.save(False, "E:/recordings")
    choice = recording.current()
    assert choice.enabled is False
    assert (choice.session_dir, choice.dump_dir) == ("", "")


def test_on_puts_both_destinations_under_the_chosen_folder(settings):
    recording.save(True, "E:/recordings")
    choice = recording.current()
    assert choice.enabled is True
    assert Path(choice.session_dir) == Path("E:/recordings") / "sessions"
    assert Path(choice.dump_dir) == Path("E:/recordings") / "dumps"


def test_enabled_with_no_folder_is_still_off(settings):
    """A half-configured switch must not guess a destination."""
    recording.save(True, "")
    assert recording.current().enabled is False


def test_a_corrupt_file_does_not_start_recording(settings):
    settings.write_text("{ not json at all", encoding="utf-8")
    recording.reset_cache()
    assert recording.current().enabled is False


def test_a_write_from_the_widget_is_noticed_without_a_restart(settings):
    """The widget edits the file directly rather than calling save()."""
    recording.save(False, "E:/recordings")
    assert recording.current().enabled is False
    settings.write_text(json.dumps({"enabled": True, "folder": "E:/recordings"}), encoding="utf-8")
    assert recording.current().enabled is True


def test_save_writes_the_exact_shape_the_widget_writes(settings):
    """Two writers, one format. Pin it so they cannot drift apart."""
    recording.save(True, "E:/recordings")
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert set(data) == {"enabled", "folder"}
