"""Sort a finished session into what was said and what was probably invented.

The worker already throws away what cannot be speech: clips with no one at the
mic, and readings that are only punctuation or a memorised "Thank you.". What
gets through is a reading of real audio, but not all of it is worth a line in
the record. On the 26 Oct meeting the readers wanted gone, without exception:
"Merci.", "What?", "Mm-hmm.", "Yeah.", "Ah, je suis..." - backchannels, words
the model put on a breath, and sentences cut off before they said anything.

Nothing is deleted. The session is split in two files, the clean copy and
everything that was taken out of it, and both keep each turn's time, so a line
that should not have gone is found in the second file at the same minute and
put back by hand. Turns are judged whole, never paragraph by paragraph, so a
long turn is never cut in half.

Aggressive on purpose: a real "Oui." to a question goes to the second file too.
A short line survives only if it names something the glossary knows - a faction,
a disposition, a player - because "Orks." or "En disruption." in a pairing
meeting is an answer, not a noise.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# A turn this short says nothing unless it names something.
MAX_SHORT_WORDS = 3

# A turn this short that trails off was cut before it said anything.
MAX_CUT_OFF_WORDS = 6

# Words a French table says out loud only as noise, or that the model writes
# for a breath. A turn made of nothing else is dropped at any length.
ENGLISH_FILLER = frozenset(
    "what yeah yes huh okay ok right bye but yep no oh ah eh hmm mm mmhmm "
    "mm-hmm uh um so and the it is you he she they other check hello hi hey "
    "don't stop wait well sure nice cool wow please sorry go if might not be "
    "work on where".split()
)

_WORD = re.compile(r"[\w'-]+")


def fold(text: str) -> str:
    """Lowercase and accent-stripped, so "Léa" and "lea" are one name."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _names(terms: Iterable[str]) -> list[re.Pattern[str]]:
    """Whole-word patterns for the terms long enough to mean something."""
    patterns = []
    for term in terms:
        folded = fold(term).strip()
        if len(folded) >= 4:
            patterns.append(re.compile(r"\b" + re.escape(folded) + r"\b"))
    return patterns


def is_probable_hallucination(text: str, names: list[re.Pattern[str]]) -> bool:
    """Is this turn a backchannel, a breath or a fragment rather than content?"""
    stripped = (text or "").strip()
    folded = fold(stripped)
    words = _WORD.findall(folded)
    if not words:
        return True
    if words and all(word in ENGLISH_FILLER for word in words):
        return True
    if any(pattern.search(folded) for pattern in names):
        return False
    if len(words) <= MAX_SHORT_WORDS:
        return True
    if stripped.endswith(("...", "…")) and len(words) <= MAX_CUT_OFF_WORDS:
        return True
    return False


def split(entries: list[dict], terms: Iterable[str]) -> tuple[list[dict], list[dict]]:
    """(kept, cut), each in the order given, each entry untouched."""
    names = _names(terms)
    kept: list[dict] = []
    cut: list[dict] = []
    for entry in entries:
        if is_probable_hallucination(entry.get("text", ""), names):
            cut.append(entry)
        else:
            kept.append(entry)
    return kept, cut
