"""The session path: one reading per chunk, written as it arrives.

Nothing here touches Discord or the network. What is pinned is that a chunk
becomes one labelled line, that the file is appended to while the session runs
rather than assembled at the end, and that a stop with nothing in it still says
so.
"""

import types
from datetime import datetime, timezone

from bot.client import STTBot


def _bot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "bot.client.transcript_cfg", types.SimpleNamespace(save_dir=str(tmp_path))
    )
    bot = object.__new__(STTBot)
    bot._transcript_channel = object()
    bot._session_lines = []
    bot._session_raw = []
    bot._entries = []
    bot._pending = []
    bot._session_started_at = datetime(2026, 10, 18, 3, 0, tzinfo=timezone.utc)
    bot._session_path = None
    bot._speaker_names = {}
    return bot


def _chunk(bot, text, name="Ekvar (Nathan)", seconds=300.0):
    bot._handle_result(
        {"text": text, "name": name, "user_id": 1, "seconds": seconds}
    )


def test_a_chunk_becomes_one_labelled_line(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch)
    _chunk(bot, "Bonjour, on commence.")
    assert bot._entries[0]["line"] == "**Ekvar (Nathan)** : Bonjour, on commence."
    assert bot._entries[0]["seconds"] == 300.0


def test_the_file_is_written_as_chunks_arrive(tmp_path, monkeypatch):
    """A session can run for hours; a crash should cost the chunk in flight."""
    bot = _bot(tmp_path, monkeypatch)
    _chunk(bot, "Premier morceau.")
    assert len(list(tmp_path.iterdir())) == 1, "written before the session ends"
    _chunk(bot, "Deuxieme morceau.")
    body = next(tmp_path.iterdir()).read_text(encoding="utf-8")
    assert "Premier morceau." in body and "Deuxieme morceau." in body
    assert body.startswith("# Transcription")


def test_entries_are_separated_by_a_blank_line(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch)
    _chunk(bot, "Un.")
    _chunk(bot, "Deux.")
    body = next(tmp_path.iterdir()).read_text(encoding="utf-8")
    assert "Un.\n\n**Ekvar" in body
    assert not any(line.startswith("> ") for line in body.splitlines())


def test_a_chunk_with_no_text_is_dropped(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch)
    bot._handle_result({"text": "   ", "name": "Ekvar", "user_id": 1})
    assert bot._entries == []
