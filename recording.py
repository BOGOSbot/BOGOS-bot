"""Where the bot is allowed to keep audio on disk, and which conversation it is.

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

One conversation, one folder. The transcript command opens it, the stop
command closes it, and everything the session writes lands inside:

    <folder>/20261017-211109/
        session.json                        who spoke, at what rate
        332976311268147202__Ekvar.wav       one uncut WAV per speaker
        dumps/0001-u332976311268147202.wav  one per utterance, as heard

Before this, a root was two flat piles - sessions/ and dumps/ - and telling one
conversation from the next meant filtering both by modification time. The folder
is named in local time, because the name is what a person reads when they go
looking for a conversation, and the bot's other session names are local too.

The bot opens the folder and the transcription worker writes into it, so the two
processes have to agree on it. They agree through live-session.json, written at
the start and removed at the end. A file rather than a queue message: the worker
can outlive a bot restart and a queue message would not, and a pointer that can
be read afterwards is one that can be checked when a dump turns up missing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import config  # noqa: F401  -- imported for its side effect: it loads .env,
# so os.getenv below sees the same values the rest of the bot does.

ROOT = Path(__file__).resolve().parent
DUMPS_SUBDIR = "dumps"
STAMP_FORMAT = "%Y%m%d-%H%M%S"


@dataclass(frozen=True)
class Choice:
    """What the user asked for, resolved once. A choice, not a session.

    The two roots are where a session folder gets created, not where audio
    lands: the stamp is only known once a conversation starts.
    """

    enabled: bool
    folder: str
    record_root: str
    dump_root: str


@dataclass(frozen=True)
class Session:
    """One conversation's folder. Everything the session writes lands in it."""

    stamp: str
    started: datetime
    directory: str
    dump_dir: str


def settings_path() -> Path:
    """Shared with the widget, which derives the same path from its own file."""
    return ROOT / "recording.json"


def session_path() -> Path:
    """The conversation now running. Written by the bot, read by the worker."""
    return ROOT / "live-session.json"


def save(enabled: bool, folder: str) -> None:
    """Write the choice. The widget writes this same shape; keep them in step."""
    settings_path().write_text(
        json.dumps({"enabled": bool(enabled), "folder": str(folder)}, indent=2),
        encoding="utf-8",
    )


def current() -> Choice:
    """The destinations the user has asked for, read fresh.

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
        record_root=record,
        dump_root=dump,
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
        return Choice(enabled=False, folder="", record_root="", dump_root="")

    if not enabled or not folder:
        return Choice(enabled=False, folder=folder, record_root="", dump_root="")

    # One root for both, so a conversation is one folder rather than two that
    # happen to share a timestamp.
    return Choice(enabled=True, folder=folder, record_root=folder, dump_root=folder)


def open_session(
    choice: Choice | None = None, started: datetime | None = None
) -> Session | None:
    """Open a folder for the conversation starting now.

    None is the answer for a switch that is off, for a root that cannot be
    created, and for a root that is not set at all. A corpus is optional; a
    session must not fail because of one.
    """
    choice = current() if choice is None else choice
    if not choice.enabled or not (choice.record_root or choice.dump_root):
        return None

    when = (started or datetime.now()).astimezone()
    stamp = when.strftime(STAMP_FORMAT)
    directory = str(Path(choice.record_root) / stamp) if choice.record_root else ""
    dump_dir = (
        str(Path(choice.dump_root) / stamp / DUMPS_SUBDIR) if choice.dump_root else ""
    )
    session = Session(stamp=stamp, started=when, directory=directory, dump_dir=dump_dir)

    try:
        for target in (directory, dump_dir):
            if target:
                Path(target).mkdir(parents=True, exist_ok=True)
        session_path().write_text(
            json.dumps(
                {
                    "stamp": stamp,
                    "started": when.isoformat(),
                    "directory": directory,
                    "dump_dir": dump_dir,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError:
        return None
    return session


def current_session() -> Session | None:
    """The folder the running conversation writes into, or None.

    Read fresh every time. A session starts and ends while the worker keeps
    running, so anything cached here would be wrong at both ends of it.
    """
    path = session_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return Session(
            stamp=str(data["stamp"]),
            started=datetime.fromisoformat(str(data["started"])),
            directory=str(data["directory"]),
            dump_dir=str(data["dump_dir"]),
        )
    except (OSError, ValueError, KeyError):
        return None


def close_session() -> None:
    """Forget the running conversation, so nothing lands in a finished folder."""
    try:
        session_path().unlink()
    except OSError:
        pass
