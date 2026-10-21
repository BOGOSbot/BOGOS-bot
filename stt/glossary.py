"""The proper nouns the transcriber should expect, and how they reach the model.

The bot transcribes a French-speaking Warhammer 40,000 tournament team: they are
reviewing army lists, agreeing pairings and matchups, and organising travel,
accommodation and payment. That is dense with names that sound like ordinary
French and are spelled nothing like it - the decoder wrote *Louvre* for Drukhari
and *ecrans* for Necrons - and the names are the part of the note that carries
the meaning.

Two plain text files, one term per line, read fresh before every request so an
edit applies to the next chunk rather than the next restart:

    glossary.txt          shipped with the repository
    glossary.local.txt    the team's own, gitignored, optional
    glossary.session.txt  this conversation's, written by the bot from the
                          pairing sheet at !pairing or !transcript, cleared
                          at !stop

The local list is for what does not belong in a public repository: their
armies, their players, the venue, the gite. The session list is what the
pairing sheet already knows - the teams, the players, the armies - so nobody
has to type it, and it is read from whichever command was handed the link,
because a new sheet is made for every meeting and the link is the only place
its names exist. Both are read after the shipped list, so a spelling that is
already known wins.

Both are attached to every chunk, so their size is paid every time. The whole
Warhammer 40,000 corpus behind [[mhq-parser]] is about 42,000 tokens, which is
95% of a one-minute chunk; the list that ships is a few hundred tokens, and it
is meant to grow from what the sessions actually get wrong rather than from a
corpus dump.

Nothing here decides *when* a term is right. The line reads "if you hear them",
because a glossary that reads as an instruction to use these words is a
glossary that invents them.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path

from config import stt_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[1]

SESSION_FILE = "glossary.session.txt"

SPELLINGS = "\n\nSpell these names this way if you hear them: {terms}."

# Past this the list has stopped being a core vocabulary and become a corpus,
# and every chunk is paying for it. Warning only: the file is the user's.
WARN_CHARS = 4000

# How long a published session list may still be adopted by a new session. A
# meeting is a few hours, so a list older than this belongs to a meeting that
# was read and never transcribed.
SESSION_MAX_AGE_SECONDS = 12 * 3600.0

_warned = False


def _resolve(name: str) -> Path | None:
    """A configured path, absolute or against the project root, or None if off."""
    name = (name or "").strip()
    if not name:
        return None
    candidate = Path(name)
    return candidate if candidate.is_absolute() else ROOT / candidate


def session_path() -> Path:
    """The names for the conversation now running.

    A file rather than a queue message, for the same reason live-session.json
    is one: the transcription worker is a different process, and a list that can
    be read afterwards is one that can be checked when a name comes back wrong.
    """
    return ROOT / SESSION_FILE


def paths() -> list[Path]:
    """Every list to read, in order. Empty when the glossary is switched off."""
    return [
        found
        for found in (
            _resolve(stt_cfg.glossary_file),
            _resolve(stt_cfg.glossary_local_file),
            session_path(),
        )
        if found is not None
    ]


def _clean(terms: Iterable[str]) -> list[str]:
    """Trimmed, in order, without repeats, and without cells too short to be a name."""
    found: list[str] = []
    seen: set[str] = set()
    for value in terms:
        term = (value or "").strip()
        key = term.casefold()
        if len(term) < 3 or key in seen:
            continue
        seen.add(key)
        found.append(term)
    return found


def set_session_terms(terms: Iterable[str]) -> int:
    """Publish the names this conversation will use, and return how many.

    Written when the session opens and cleared when it closes, so a name from
    one tournament cannot colour the next. Empty clears it.
    """
    cleaned = _clean(terms)
    path = session_path()
    try:
        if not cleaned:
            path.unlink(missing_ok=True)
            return 0
        # Write beside it and replace: the worker reads this between chunks, and
        # a half-written file would be read as a shorter list.
        scratch = path.with_name(path.name + ".tmp")
        scratch.write_text("\n".join(cleaned) + "\n", encoding="utf-8")
        os.replace(scratch, path)
    except OSError as exc:
        logger.warning("Could not write %s: %s", path, exc)
        return 0
    return len(cleaned)


def session_terms() -> list[str]:
    """The names this conversation can still adopt, or [] if there are none.

    The pairing sheet is per meeting: a new one is made for every tournament, so
    the link is the only place its names exist and a list read for one meeting
    must not reach the next. A name from the wrong tournament is exactly what a
    glossary is meant to prevent. !stop clears the file, so what is left is
    either this meeting's list or one that was read and never used, and the
    file's own age tells the two apart without anything written into it.

    This decides whether to *adopt* a list, not whether to keep reading one: a
    session that has started keeps its names however long the meeting runs.
    """
    path = session_path()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return []
    if age > SESSION_MAX_AGE_SECONDS:
        logger.info(
            "Ignoring a session glossary %.0f hours old; it belongs to a "
            "meeting that was never transcribed.",
            age / 3600.0,
        )
        return []
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    return _clean(
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#")
    )


def terms() -> list[str]:
    """The vocabulary in force right now, in file order and without repeats.

    Read fresh every time. The list is meant to be tuned between sessions, and
    a cached copy would mean restarting the bot to add a word.
    """
    found: list[str] = []
    seen: set[str] = set()
    for source in paths():
        if not source.is_file():
            continue
        try:
            # utf-8-sig: an editor on Windows will happily save this file with a
            # byte-order mark, and the first term would carry it into the prompt.
            raw = source.read_text(encoding="utf-8-sig")
        except OSError as exc:
            logger.warning("Could not read the glossary %s: %s", source, exc)
            continue
        for line in raw.splitlines():
            term = line.strip()
            if not term or term.startswith("#"):
                continue
            key = term.casefold()
            if key in seen:
                continue
            seen.add(key)
            found.append(term)
    return found


def append_to(prompt: str) -> str:
    """The prompt with the vocabulary attached, or unchanged if there is none."""
    global _warned
    found = terms()
    if not found:
        return prompt

    line = ", ".join(found)
    if len(line) > WARN_CHARS and not _warned:
        _warned = True
        logger.warning(
            "The glossary is %d characters, about %d tokens, and is sent with "
            "every chunk. Consider scoping it.",
            len(line),
            len(line) // 4,
        )
    return prompt + SPELLINGS.format(terms=line)
