"""The proper nouns the transcriber should expect, and how they reach the model.

A game of Warhammer 40,000 is dense with names that sound like ordinary French
and are spelled nothing like it: the decoder wrote *Louvre* for Drukhari and
*ecrans* for Necrons. Naming the game in the prompt already fixed part of that.
This carries the vocabulary itself.

A plain text file, one term per line, read fresh before every request so an
edit applies to the next chunk rather than the next restart. It is attached to
every chunk, so its size is paid every time: it holds the vocabulary of the
game in general, not of any one army. Scoping it by faction was measured at
about 3,000 tokens for the three armies of one session, against roughly 340 for
the list that ships, and the list is meant to grow from what the sessions
actually get wrong rather than from a corpus dump.

Nothing here decides *when* a term is right. The line reads "if you hear them",
because a glossary that reads as an instruction to use these words is a
glossary that invents them.
"""

from __future__ import annotations

from pathlib import Path

from config import stt_cfg
from utils.logging import get_logger

logger = get_logger(__name__)

ROOT = Path(__file__).resolve().parents[1]

SPELLINGS = "\n\nSpell these names this way if you hear them: {terms}."

# Past this the list has stopped being a core vocabulary and become a corpus,
# and every chunk is paying for it. Warning only: the file is the user's.
WARN_CHARS = 4000

_warned = False


def path() -> Path | None:
    """Where the list lives, or None when it is switched off."""
    name = (stt_cfg.glossary_file or "").strip()
    if not name:
        return None
    candidate = Path(name)
    return candidate if candidate.is_absolute() else ROOT / candidate


def terms() -> list[str]:
    """The vocabulary in force right now, in file order and without repeats.

    Read fresh every time. The list is meant to be tuned between sessions, and
    a cached copy would mean restarting the bot to add a word.
    """
    source = path()
    if source is None or not source.is_file():
        return []
    try:
        # utf-8-sig: an editor on Windows will happily save this file with a
        # byte-order mark, and the first term would carry it into the prompt.
        raw = source.read_text(encoding="utf-8-sig")
    except OSError as exc:
        logger.warning("Could not read the glossary %s: %s", source, exc)
        return []

    found: list[str] = []
    seen: set[str] = set()
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
            "every chunk. Consider scoping it by faction.",
            len(line),
            len(line) // 4,
        )
    return prompt + SPELLINGS.format(terms=line)
