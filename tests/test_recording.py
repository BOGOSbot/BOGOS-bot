"""The recording switch: one file decides what reaches the disk."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import recording


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """A recording.json and a session pointer of our own, no inherited env."""
    path = tmp_path / "recording.json"
    monkeypatch.setattr(recording, "settings_path", lambda: path)
    monkeypatch.setattr(recording, "session_path", lambda: tmp_path / "live-session.json")
    monkeypatch.delenv("RECORD_DIR", raising=False)
    monkeypatch.delenv("STT_DUMP_DIR", raising=False)
    yield path


def _at(hour: int, minute: int) -> datetime:
    """A naive local time, so the stamp is the same in every timezone."""
    return datetime(2026, 10, 17, hour, minute, 9)


def test_without_the_file_the_environment_still_decides(settings, monkeypatch):
    """An existing .env must keep behaving exactly as it did before."""
    monkeypatch.setenv("STT_DUMP_DIR", "dumps")
    choice = recording.current()
    assert choice.dump_root == "dumps"
    assert choice.record_root == ""


def test_off_overrides_the_environment(settings, monkeypatch):
    """The whole point of the switch: off beats a leftover .env value."""
    monkeypatch.setenv("RECORD_DIR", "E:/recordings")
    monkeypatch.setenv("STT_DUMP_DIR", "dumps")
    recording.save(False, "E:/recordings")
    choice = recording.current()
    assert choice.enabled is False
    assert (choice.record_root, choice.dump_root) == ("", "")


def test_on_puts_both_roots_under_the_chosen_folder(settings):
    recording.save(True, "E:/recordings")
    choice = recording.current()
    assert choice.enabled is True
    assert Path(choice.record_root) == Path("E:/recordings")
    assert Path(choice.dump_root) == Path("E:/recordings")


def test_enabled_with_no_folder_is_still_off(settings):
    """A half-configured switch must not guess a destination."""
    recording.save(True, "")
    assert recording.current().enabled is False


def test_a_corrupt_file_does_not_start_recording(settings):
    settings.write_text("{ not json at all", encoding="utf-8")
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


# -- one conversation, one folder --------------------------------------------


def test_a_session_gets_its_own_timestamped_folder(settings, tmp_path):
    root = tmp_path / "recordings"
    recording.save(True, str(root))
    session = recording.open_session(started=_at(21, 11))

    assert session.stamp == "20261017-211109"
    assert Path(session.directory) == root / "20261017-211109"
    assert Path(session.dump_dir) == root / "20261017-211109" / "dumps"
    assert Path(session.directory).is_dir()
    assert Path(session.dump_dir).is_dir()


def test_two_sessions_do_not_share_a_folder(settings, tmp_path):
    root = tmp_path / "recordings"
    recording.save(True, str(root))
    first = recording.open_session(started=_at(21, 11))
    second = recording.open_session(started=_at(22, 40))

    assert first.directory != second.directory
    assert Path(first.directory).is_dir()
    assert Path(second.directory).is_dir()


def test_the_worker_can_read_the_folder_the_bot_opened(settings, tmp_path):
    """The two processes agree through the pointer, not through a queue."""
    root = tmp_path / "recordings"
    recording.save(True, str(root))
    opened = recording.open_session(started=_at(21, 11))

    assert recording.current_session() == opened


def test_closing_forgets_the_folder_without_erasing_it(settings, tmp_path):
    root = tmp_path / "recordings"
    recording.save(True, str(root))
    opened = recording.open_session(started=_at(21, 11))

    recording.close_session()
    assert recording.current_session() is None
    assert Path(opened.directory).is_dir()
    recording.close_session()  # idempotent


def test_off_opens_no_folder(settings, tmp_path):
    root = tmp_path / "recordings"
    recording.save(False, str(root))
    assert recording.open_session(started=_at(21, 11)) is None
    assert not root.exists()
    assert recording.current_session() is None


def test_a_root_it_cannot_create_is_survivable(settings, tmp_path):
    """A corpus is optional; the session is not."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    recording.save(True, str(blocker))
    assert recording.open_session(started=_at(21, 11)) is None


def test_a_corrupt_pointer_reads_as_no_session(settings, tmp_path):
    recording.session_path().write_text("{ not json", encoding="utf-8")
    assert recording.current_session() is None


def test_the_environment_roots_each_get_a_folder(settings, tmp_path, monkeypatch):
    """An .env names two destinations; each still gets one folder per session."""
    record = tmp_path / "recordings"
    dump = tmp_path / "dumps"
    monkeypatch.setenv("RECORD_DIR", str(record))
    monkeypatch.setenv("STT_DUMP_DIR", str(dump))

    session = recording.open_session(started=_at(21, 11))

    assert Path(session.directory) == record / "20261017-211109"
    assert Path(session.dump_dir) == dump / "20261017-211109" / "dumps"


def test_a_dump_only_root_still_opens_a_folder(settings, tmp_path, monkeypatch):
    """STT_DUMP_DIR alone means utterances and no continuous audio."""
    monkeypatch.setenv("STT_DUMP_DIR", str(tmp_path / "dumps"))
    session = recording.open_session(started=_at(21, 11))

    assert session.directory == ""
    assert Path(session.dump_dir).is_dir()
