"""The proper nouns the transcriber should expect, and how they reach the model.

The bot transcribes a French-speaking Warhammer 40,000 tournament team: they are
reviewing army lists, agreeing pairings and matchups, and organising travel,
accommodation and payment. That is dense with names that sound like ordinary
French and are spelled nothing like it - the decoder wrote *Louvre* for Drukhari
and *ecrans* for Necrons - and the names are the part of the note that carries
the meaning.

Two plain text files, one term per line, read fresh before every request so an
edit applies to the next chunk rather than the next restart:

    glossary.txt        shipped with the repository
    glossary.local.txt  the team's own, gitignored, optional

The second is for what does not belong in a public repository: their armies,
their players, the venue, the gite. It is read after the first, so the shipped
spelling wins a duplicate.

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


def _resolve(name: str) -> Path | None:
    """A configured path, absolute or against the project root, or None if off."""
    name = (name or "").strip()
    if not name:
        return None
    candidate = Path(name)
    return candidate if candidate.is_absolute() else ROOT / candidate


def paths() -> list[Path]:
    """Every list to read, in order. Empty when the glossary is switched off."""
    return [
        found
        for found in (
            _resolve(stt_cfg.glossary_file),
            _resolve(stt_cfg.glossary_local_file),
        )
        if found is not None
    ]


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
