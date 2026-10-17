"""The transcript file must survive Discord's markdown renderer.

Two ways it did not: a line beginning "> " opens a blockquote that swallows
every line after it until a blank one, and consecutive lines with nothing
between them collapse into a single paragraph. Both flattened the session.
"""

import types
from datetime import datetime, timezone

from bot.client import STTBot


def _write(tmp_path, lines):
    bot = object.__new__(STTBot)
    entries = [{"name": "A", "final": l} for l in lines]
    bot._save_session(entries, lines, datetime(2026, 10, 17, 19, 40, tzinfo=timezone.utc))
    return (tmp_path / "transcription-2026-10-17-1940.md").read_text(encoding="utf-8")


def test_no_line_opens_a_blockquote(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bot.client.transcript_cfg", types.SimpleNamespace(save_dir=str(tmp_path))
    )
    body = _write(tmp_path, ["**A** : one", "**B** : two"])
    assert not any(l.startswith("> ") for l in body.splitlines())


def test_entries_are_separated_by_a_blank_line(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bot.client.transcript_cfg", types.SimpleNamespace(save_dir=str(tmp_path))
    )
    body = _write(tmp_path, ["**A** : one", "**B** : two"])
    assert "**A** : one\n\n**B** : two" in body


def test_a_blockquote_never_starts_a_body():
    """The variant line is what broke it, so the body must never build one."""
    import inspect
    from bot import client
    src = inspect.getsource(client.STTBot._handle_result)
    assert "> *" not in src


def test_the_file_has_no_counts_line(tmp_path, monkeypatch):
    """Redundant with the brackets, and wrong for a while besides."""
    monkeypatch.setattr(
        "bot.client.transcript_cfg", types.SimpleNamespace(save_dir=str(tmp_path))
    )
    bot = object.__new__(STTBot)
    lines = [
        "**A** : il fait (quatre | 4)",
        "**B** : une phrase sans conflit",
        "**C** : (∅ | Le 1er janvier 2010)",
    ]
    bot._save_session(
        [{"name": n, "final": l} for n, l in zip("ABC", lines)],
        lines,
        datetime(2026, 10, 17, 21, 11, tzinfo=timezone.utc),
    )
    body = (tmp_path / "transcription-2026-10-17-2111.md").read_text(encoding="utf-8")
    assert "utterances" not in body
    assert "with a dispute" not in body
    assert body.startswith("# Transcription")
    assert body.count("**") == 6
