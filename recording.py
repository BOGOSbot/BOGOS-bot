"""Where the bot is allowed to keep audio on disk.

Two things write voices to disk. The session recorder keeps one uncut WAV per
speaker so a corpus can be rebuilt and the VAD re-tuned on identical material.
The per-utterance dumps keep exactly what the model heard. Both captured
everyone in the channel, and the dumps did it quietly, because STT_DUMP_DIR was
set and nothing ever surfaced that.

The widget and the bot share one file, recording.json:

    {"enabled": true, "folder": "E:\\\\recordings"}

The widget writes it; the bot reads it every time it needs a destination, so a
toggle applies to the next session instead of needing a restart. With no file
the environment still decides exactly as it did before this module existed, so
an existing .env keeps working.

Enabled puts both destinations under the chosen folder:

    <folder>/sessions/   one WAV per speaker per session, plus session.json
    <folder>/dumps/      one WAV per utterance, named by sequence and user id

Disabled writes neither, and overrides the environment. That override is the
point: a switch that says off has to mean off, however .env was left.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import config  # noqa: F401  -- imported for its side effect: it loads .env,
# so os.getenv below sees the same values the rest of the bot does.

ROOT = Path(__file__).resolve().parent
SESSIONS_SUBDIR = "sessions"
DUMPS_SUBDIR = "dumps"

@dataclass(frozen=True)
class Choice:
    """The four things a caller can ask about, resolved once."""

    enabled: bool
    folder: str
    session_dir: str
    dump_dir: str


def settings_path() -> Path:
    """Shared with the widget, which derives the same path from its own file."""
    return ROOT / "recording.json"


def save(enabled: bool, folder: str) -> None:
    """Write the choice. The widget writes this same shape; keep them in step."""
    settings_path().write_text(
        json.dumps({"enabled": bool(enabled), "folder": str(folder)}, indent=2),
        encoding="utf-8",
    )


def current() -> Choice:
    """A destination to use right now, read fresh.

    An earlier version cached on the file's mtime. A stat carries clock-tick
    granularity, so two writes inside one tick look identical and the cache then
    serves a stale answer at exactly the moment someone flipped the switch. The
    file is two keys and the callers are rare, so the cache bought microseconds
    against a two-second transcription and risked being wrong.
    """
    path = settings_path()
    if not path.exists():
        return _from_environment()
    return _resolve(path)


def _from_environment() -> Choice:
    """No widget choice yet: the environment decides, as it always has."""
    record = os.getenv("RECORD_DIR", "")
    dump = os.getenv("STT_DUMP_DIR", "")
    return Choice(
        enabled=bool(record or dump),
        folder="",
        session_dir=record,
        dump_dir=dump,
    )


def _resolve(path: Path) -> Choice:
    try:
        # utf-8-sig, not utf-8: PowerShell's Set-Content writes a BOM, and a
        # BOM would make this raise, which reads as "off" rather than as a bug.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        enabled = bool(data.get("enabled"))
        folder = str(data.get("folder") or "")
    except (OSError, ValueError):
        # A file we cannot read must not quietly start recording.
        return Choice(enabled=False, folder="", session_dir="", dump_dir="")

    if not enabled or not folder:
        return Choice(enabled=False, folder=folder, session_dir="", dump_dir="")

    base = Path(folder)
    return Choice(
        enabled=True,
        folder=folder,
        session_dir=str(base / SESSIONS_SUBDIR),
        dump_dir=str(base / DUMPS_SUBDIR),
    )
