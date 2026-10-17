"""The end-of-session path: reconcile, post, keep both readings.

Nothing here touches Discord or the network. What is being pinned is that the
mechanical line survives a failing model, that a line the model calls fabricated
disappears, and that the session is written to disk with both readings in it.
"""

import types
from datetime import datetime, timezone

from bot.client import STTBot
import config


def _bot():
    # Bypass __init__: none of the methods under test touch Discord.
    return object.__new__(STTBot)


def _entries():
    return [
        {"name": "Nathan", "primary": "il fait quatre", "second": "il fait 4",
         "body": "il fait quatre", "line": "**Nathan** : il fait quatre"},
        {"name": "Nathan", "primary": "battle shop", "second": "battle shock",
         "body": "battle (shop | shock)", "line": "**Nathan** : battle (shop | shock)"},
        {"name": "Thomas", "primary": "", "second": "Le 1er janvier 2010 …",
         "body": "(∅ | Le 1er janvier 2010 …)",
         "line": "**Thomas** : (∅ | Le 1er janvier 2010 …)"},
    ]


def test_a_failed_model_leaves_the_brackets_standing(monkeypatch):
    """The safe direction: a rough transcript, never a missing one."""
    monkeypatch.setattr(
        "bot.client.reconcile_cfg",
        types.SimpleNamespace(enabled=False, base_url="", api_key="", model="", timeout_seconds=1),
    )
    lines = _bot()._reconcile(_entries())
    assert lines[0] == "**Nathan** : il fait quatre"
    assert lines[1] == "**Nathan** : battle (shop | shock)"
    assert len(lines) == 3


def test_the_model_settles_a_line_and_drops_a_fabrication(monkeypatch):
    monkeypatch.setattr(
        "bot.client.reconcile_cfg",
        types.SimpleNamespace(
            enabled=True, base_url="u", api_key="k", models=["m"],
            timeout_seconds=1, reasoning_effort="low",
        ),
    )
    monkeypatch.setattr(
        "stt.reconciler.reconcile",
        lambda *a, **k: types.SimpleNamespace(
            replacements={2: "battle shock", 3: ""}, error=""
        ),
    )
    lines = _bot()._reconcile(_entries())
    assert lines == ["**Nathan** : il fait quatre", "**Nathan** : battle shock"]


def test_the_session_is_written_with_both_readings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "bot.client.transcript_cfg", types.SimpleNamespace(save_dir=str(tmp_path))
    )
    entries = _entries()
    _bot()._save_session(entries, ["**Nathan** : merged"], datetime(2026, 10, 17, 18, 31, tzinfo=timezone.utc))

    written = sorted(p.name for p in tmp_path.iterdir())
    assert written == [
        "transcription-2026-10-17-1831.json",
        "transcription-2026-10-17-1831.md",
    ]
    payload = (tmp_path / written[0]).read_text(encoding="utf-8")
    assert "battle shop" in payload and "battle shock" in payload
    assert "Le 1er janvier" in payload
    assert "merged" in (tmp_path / written[1]).read_text(encoding="utf-8")



def test_the_speaker_name_survives_the_reconciler(monkeypatch):
    """The model is handed words only. It drops "**Name** :" as formatting."""
    monkeypatch.setattr(
        "bot.client.reconcile_cfg",
        types.SimpleNamespace(
            enabled=True, base_url="u", api_key="k", models=["m"],
            timeout_seconds=1, reasoning_effort="low",
        ),
    )
    monkeypatch.setattr(
        "stt.reconciler.reconcile",
        lambda *a, **k: types.SimpleNamespace(
            replacements={2: "battle shock"}, error=""
        ),
    )
    lines = _bot()._reconcile(_entries())
    assert lines[1] == "**Nathan** : battle shock"
    assert lines[0] == "**Nathan** : il fait quatre"



def test_the_reviewed_file_does_not_overwrite_the_mechanical_one(monkeypatch, tmp_path):
    """Both are posted, so both have to exist; only the reviewed one is final."""
    monkeypatch.setattr(
        "bot.client.transcript_cfg", types.SimpleNamespace(save_dir=str(tmp_path))
    )
    bot = _bot()
    stamp = datetime(2026, 10, 17, 18, 31, tzinfo=timezone.utc)
    bot._save_session(_entries(), ["**Nathan** : raw"], stamp)
    bot._save_session(_entries(), ["**Nathan** : reviewed"], stamp, reviewed=True)

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [
        "transcription-2026-10-17-1831-reviewed.json",
        "transcription-2026-10-17-1831-reviewed.md",
        "transcription-2026-10-17-1831.json",
        "transcription-2026-10-17-1831.md",
    ]
    assert "reviewed" in (
        tmp_path / "transcription-2026-10-17-1831-reviewed.md"
    ).read_text(encoding="utf-8")
    assert "raw" in (
        tmp_path / "transcription-2026-10-17-1831.md"
    ).read_text(encoding="utf-8")


def test_a_marker_the_model_echoes_back_is_stripped(monkeypatch):
    """One arrived on lines that held no bracket; nothing decorative survives."""
    monkeypatch.setattr(
        "bot.client.reconcile_cfg",
        types.SimpleNamespace(
            enabled=True, base_url="u", api_key="k", models=["m"],
            timeout_seconds=1, reasoning_effort="low",
        ),
    )
    monkeypatch.setattr(
        "stt.reconciler.reconcile",
        lambda *a, **k: types.SimpleNamespace(
            replacements={1: "il fait quatre \u26a0", 2: "battle shock \u26a0 extra"},
            error="",
        ),
    )
    lines = _bot()._reconcile(_entries())
    assert "\u26a0" not in "".join(lines)
    assert lines[0] == "**Nathan** : il fait quatre"
