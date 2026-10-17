"""Where two models read the same audio differently.

The bot runs a second model only to find these. When the two agree the reader
sees one line; when they differ the reader sees both and decides. No model
judges another here, deliberately: the failure that matters is fluent (voxtral
wrote "ecrans" for Necrons, "spiders" for Speeders), so a judge reading text
alone would prefer the wrong reading. The decision needs an ear.

Comparison runs on NORMALISED tokens, not raw text. On 181 s of real speech the
raw difference was 55 spans; about 20 of those were digits against words
("trois" vs "3") and one was "6+" against "six". Those are formatting, not
conflict, and leaving them in would bury the ones that matter.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

# Number words to digits, so "trois" and "3" compare equal. Un/une map too:
# they are also the articles, but the mapping is symmetric so it can only hide
# a difference where the two models already agree on the sound.
_NUMBERS = {
    "zero": "0", "un": "1", "une": "1", "deux": "2", "trois": "3", "quatre": "4",
    "cinq": "5", "six": "6", "sept": "7", "huit": "8", "neuf": "9", "dix": "10",
    "onze": "11", "douze": "12", "treize": "13", "quatorze": "14", "quinze": "15",
    "seize": "16", "vingt": "20", "trente": "30", "quarante": "40",
    "cinquante": "50", "soixante": "60", "cent": "100",
}

_NON_WORD = re.compile(r"[^\w]", re.UNICODE)


@dataclass(frozen=True)
class Difference:
    """One span, as each model heard it."""

    primary: str
    other: str


def normalise(token: str) -> str:
    """Case, accents, punctuation and number spelling removed."""
    flat = unicodedata.normalize("NFKD", token.lower())
    flat = "".join(c for c in flat if not unicodedata.combining(c))
    flat = _NON_WORD.sub("", flat)
    return _NUMBERS.get(flat, flat)


def normalise_tokens(text: str) -> list[str]:
    """The comparison units of a whole line."""
    return [normalise(t) for t in text.split()]


def compare(primary: str, other: str) -> list[Difference]:
    """The spans where two readings differ, ignoring formatting."""
    left, right = primary.split(), other.split()
    lnorm = [normalise(t) for t in left]
    rnorm = [normalise(t) for t in right]

    matcher = difflib.SequenceMatcher(None, lnorm, rnorm, autojunk=False)
    found: list[Difference] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # Punctuation alone normalises away on both sides: not a conflict.
        if not any(lnorm[i1:i2]) and not any(rnorm[j1:j2]):
            continue
        found.append(Difference(" ".join(left[i1:i2]), " ".join(right[j1:j2])))
    return found


def divergence(primary: str, other: str) -> float:
    """The share of the line the two readings disagree about.

    Measured on six real table-talk utterances: the ones where the models are
    choosing between words sit at 7-13%, and the ones where they rebuild the
    sentence sit at 28-29%. A span count cannot tell those apart, because a
    long line can carry many small edits and a short one a single fatal word.
    """
    left, right = normalise_tokens(primary), normalise_tokens(other)
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    changed = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
    return changed / max(len(left), len(right), 1)


def describe(differences: list[Difference], cap: int = 6) -> str:
    """The contested spans, for a reader deciding between two words."""
    if not differences:
        return ""
    shown = differences[:cap]
    parts = ["%s → %s" % (d.primary or "rien", d.other or "rien") for d in shown]
    hidden = len(differences) - len(shown)
    if hidden > 0:
        parts.append("+%d autre(s)" % hidden)
    return " ; ".join(parts)


# Two models agreeing is not the same as them saying the same thing, but only
# two outcomes are safe to decide automatically:
#
#   SAME    wording differs, meaning does not ("je ne pense pas" / "je pense
#           pas"). Both readings say the same thing, so printing one loses
#           nothing and saves the reader an eye movement.
#   DIFFER  a real disagreement, and the reader decides. This includes pairs
#           that share no content at all.
#
# There is deliberately no third outcome that drops a pair. An earlier
# version added one for "nothing in common" and cut those lines, which was
# wrong: "J'ai remis mes points" against "Jean-Remy Dupont" shares nothing, and
# one of those is real speech. Only a reader can tell that apart from a
# fabrication, and a rule that guesses costs the reader words they never get
# to see. Fabrications are caught separately, by the guards built for them.

# Function words, fillers and contractions. A difference confined to these is
# style, not content: they carry grammar and hesitation, not meaning.
_FUNCTION = frozenset(
    """
    le la les l un une des du de d au aux a et ou ni mais car donc or que qu qui
    quoi je j tu te toi il elle ils elles on nous vous me m se s ce c cet cette
    ces ne n pas plus moins tres trop peu y en dans sur sous par pour avec sans
    est sont etait ete etre avoir ai as ont avait avais suis es sommes etes
    mon ma mes ton ta tes son sa ses notre nos votre vos leur leurs
    si comme quand hmm hum heu euh bah ben bon ok ouais oui non mm mhm hein
    ah oh eh bien voila ca ici la bas
    """
.split()
)


class Verdict(str, Enum):
    """What to do with a pair of readings."""

    SAME = "same"
    DIFFER = "differ"


def _is_content(token: str) -> bool:
    """A word that carries meaning, not grammar."""
    return len(token) > 1 and token not in _FUNCTION


def classify(primary: str, other: str) -> Verdict:
    """SAME only when nothing but grammar and filler moved."""
    if not compare(primary, other):
        return Verdict.SAME
    left, right = normalise_tokens(primary), normalise_tokens(other)
    moved = [t for t in set(left) ^ set(right) if t]
    if moved and all(not _is_content(t) for t in moved):
        return Verdict.SAME
    return Verdict.DIFFER
