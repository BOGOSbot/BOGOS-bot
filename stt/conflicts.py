"""Where two models read the same audio differently.

The bot runs a second model only to find these. When the two agree the reader
sees one line; when they disagree the reader sees one line with each contested
word bracketed, and decides. No model judges another here, deliberately: the
failure that matters is fluent (voxtral wrote "ecrans" for Necrons, "spiders"
for Speeders), so a judge reading text alone would prefer the wrong reading.
The decision needs an ear.

Comparison runs on NORMALISED tokens for deciding whether there is a conflict at
all, so that mechanical differences do not bury the real ones. Rendering then
returns to the original words, because a reader judging a line needs to see what
was actually written, not a normalised shadow of it.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

# Number words to digits, so "trois" and "3" compare equal when deciding whether
# two readings conflict. Un/une map too: they are also the articles, but the
# mapping is symmetric so it can only hide a difference where the two models
# already agree on the sound.
_NUMBERS = {
    "zero": "0", "un": "1", "une": "1", "deux": "2", "trois": "3", "quatre": "4",
    "cinq": "5", "six": "6", "sept": "7", "huit": "8", "neuf": "9", "dix": "10",
    "onze": "11", "douze": "12", "treize": "13", "quatorze": "14", "quinze": "15",
    "seize": "16", "vingt": "20", "trente": "30", "quarante": "40",
    "cinquante": "50", "soixante": "60", "cent": "100",
}

_NON_WORD = re.compile(r"[^\w]", re.UNICODE)
_PUNCT = "\"'()[]{}<>,.;:!?«»…-–—/\\"


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
    sentence sit at 28-29%.
    """
    left, right = normalise_tokens(primary), normalise_tokens(other)
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    changed = sum(
        max(i2 - i1, j2 - j1)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )
    return changed / max(len(left), len(right), 1)


# Words are compared with punctuation stripped when rendering: two readings that
# differ only in how they punctuate are the same reading, and marking it would
# bury the words that actually moved. Spelling is kept, so "douze" and "12" both
# show, because a reader may well care which one was said.
def _bare(token: str) -> str:
    return token.strip(_PUNCT)


def _trail(token: str) -> str:
    return token[len(token.rstrip(_PUNCT)):]


# Spelling families that are not speech differences, and that the reconciler
# can therefore merge without judgement: a number written out or in digits, a
# plural, an elided clitic. Measured on this session they account for most of
# the "same meaning" class, which is work an LLM should never be paid for.
# A clitic expands to the word it elides. Keeping the particle rather than
# dropping it makes "t'as" and "tu as" the same string once flattened, which is
# what they are.
_ELIDE = {
    "j": "je", "t": "tu", "s": "se", "m": "me", "n": "ne", "l": "le",
    "d": "de", "c": "ce", "qu": "que", "jusqu": "jusque", "lorsqu": "lorsque",
    "puisqu": "puisque", "quoiqu": "quoique",
}


def _mech(token: str) -> str:
    """A spelling-insensitive form of one token."""
    flat = _fold(token)
    for mark in ("'", "\u2019"):
        if mark in flat:
            head, tail = flat.split(mark, 1)
            flat = _ELIDE.get(head, head) + tail
            break
    flat = _NON_WORD.sub("", flat)
    flat = _NUMBERS.get(flat, flat)
    if len(flat) > 3 and flat.endswith("s"):
        flat = flat[:-1]
    return flat


def _fold(text: str) -> str:
    flat = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in flat if not unicodedata.combining(ch))


def _same_thing(left: list[str], right: list[str]) -> bool:
    """True when two spans differ only in how they are written down."""
    if not left and not right:
        return True
    a = [_mech(t) for t in left]
    b = [_mech(t) for t in right]
    if "".join(a) == "".join(b):
        return True
    # A model that stops mid-word leaves a prefix: "demi" against "demi-femme".
    if len(a) == len(b) and a:
        return all(
            x == y or (len(x) >= 4 and len(y) >= 4 and (x.startswith(y) or y.startswith(x)))
            for x, y in zip(a, b)
        )
    return False


def _key(token: str) -> str:
    """Alignment key: case, accents and punctuation gone, spelling kept."""
    flat = unicodedata.normalize("NFKD", _bare(token).lower())
    flat = "".join(ch for ch in flat if not unicodedata.combining(ch))
    # Inner punctuation too. A clitic keeps its apostrophe in the middle, so
    # stripping only the edges left "C'est" and "C'’est" looking like different
    # words when the only difference is which apostrophe the model emitted.
    return _NON_WORD.sub("", flat)


# Elided clitics. "qu'il" and "il" are the same word to a listener, and a
# model that restarts a sentence often drops the elision on the repeat, which
# made an otherwise obvious stutter invisible to a strict comparison.
_ELISIONS = ("qu", "l", "d", "j", "m", "t", "s", "n", "c")


def _similar(left: str, right: str) -> bool:
    """Equal, or equal once an elided clitic is set aside."""
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    if len(short) < 2 or not long.endswith(short):
        return False
    return long[: len(long) - len(short)] in _ELISIONS


def _matches(span: list[str], unit: list[str]) -> bool:
    """True when the span repeats the unit, allowing an elision per word."""
    return len(span) == len(unit) and all(
        _similar(a, b) for a, b in zip(span, unit)
    )


def _repeats(keys: list[str], start: int, end: int) -> bool:
    """True when keys[start:end] is a phrase stuttered from beside it.

    The repeated unit can be shorter than the span: "Oh, oh, oh," is "oh,"
    three times, and the run that follows it may be one token long. Checking
    only same-length neighbours missed that, so this looks at every period that
    divides the span.
    """
    span = keys[start:end]
    size = len(span)
    if not size:
        return False
    for period in range(1, size + 1):
        if size % period:
            continue
        after = keys[end:end + period]
        if len(after) == period and _matches(span, after * (size // period)):
            return True
        before = keys[max(0, start - period):start]
        if len(before) == period and _matches(span, before * (size // period)):
            return True
    return False


def render(primary: str, other: str) -> str:
    """One line, with each disagreement bracketed at the word that moved.

    "Ca me va. Sophie, tu fais douze, sale batard." against "Ca me va. Sauf si
    tu fais 12. Sale batard." becomes

        Ca me va. (Sophie, | Sauf si) tu fais (douze | 12), sale batard.

    Punctuation that both sides carry and differ only in kind moves outside the
    brackets once, so a comma against a full stop never becomes a difference.
    A word only one side heard is marked against an empty side.
    """
    left, right = primary.split(), other.split()
    if not left or not right:
        return primary or other

    left_keys = [_key(t) for t in left]
    right_keys = [_key(t) for t in right]
    matcher = difflib.SequenceMatcher(None, left_keys, right_keys, autojunk=False)

    out: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            out.append(" ".join(left[i1:i2]))
            continue

        primary_span, other_span = list(left[i1:i2]), list(right[j1:j2])

        # A hesitation sitting in front of the real difference belongs in plain
        # text, not inside the bracket. "ouais. Necrozor" against "Necrozard"
        # should bracket the name, not the hesitation before it.
        while (
            len(primary_span) > len(other_span)
            and not _is_content(_key(primary_span[0]))
        ):
            out.append(primary_span.pop(0))

        # One model repeating a phrase the other said once is a stutter, not a
        # different reading. Bracketing it puts identical words on both sides of
        # the bracket, which reads as a mistake. Show the phrase once.
        if tag == "delete" and _repeats(left_keys, i1, i2):
            continue
        if tag == "insert" and _repeats(right_keys, j1, j2):
            continue

        if _same_thing(primary_span, other_span):
            out.append(" ".join(primary_span) or " ".join(other_span))
            continue

        # Same letters, split differently: "A2" against "à 2", "tues" against
        # "tu es". The reader hears one word either way, so the primary's
        # spelling stands and nothing is bracketed.
        if "".join(_key(t) for t in primary_span) == "".join(
            _key(t) for t in other_span
        ) and "".join(_key(t) for t in primary_span):
            out.append(" ".join(primary_span))
            continue

        # Nothing but grammar and filler moved here. "Ah" against silence is a
        # real omission but not one a reader has to weigh, and bracketing it in
        # a line already flagged for something else is noise. Keep the primary's
        # words so the sentence still reads, and mark nothing.
        primary_content = [t for t in primary_span if _is_content(_key(t))]
        other_content = [t for t in other_span if _is_content(_key(t))]
        if not primary_content and not other_content:
            if primary_span:
                out.append(" ".join(primary_span))
            continue

        # Standalone punctuation arrives as its own token ("truc !" against
        # "truc"). Neither side carries a word here, so there is nothing to
        # judge; keep the primary's mark and move on.
        if not any(_key(t) for t in primary_span) and not any(
            _key(t) for t in other_span
        ):
            if primary_span:
                out.append(" ".join(primary_span))
            continue
        trail_p = _trail(primary_span[-1]) if primary_span else ""
        trail_o = _trail(other_span[-1]) if other_span else ""

        if trail_p and trail_o:
            bare_p = " ".join(t for t in (_bare(x) for x in primary_span) if t)
            bare_o = " ".join(t for t in (_bare(x) for x in other_span) if t)
            out.append("(%s | %s)%s" % (bare_p or "∅", bare_o or "∅", trail_p))
        else:
            out.append(
                "(%s | %s)"
                % (" ".join(primary_span) or "∅", " ".join(other_span) or "∅")
            )
    return " ".join(out)


# Two models agreeing is not the same as them saying the same thing, but only
# two outcomes are safe to decide automatically:
#
#   SAME    wording differs, meaning does not ("je ne pense pas" / "je pense
#           pas"). Both readings say the same thing, so printing one loses
#           nothing and saves the reader an eye movement.
#   DIFFER  a real disagreement, and the reader decides. This includes pairs
#           that share no content at all.
#
# There is deliberately no third outcome that drops a pair. An earlier version
# added one for "nothing in common" and cut those lines, which was wrong:
# "J'ai remis mes points" against "Jean-Remy Dupont" shares nothing, and one of
# those is real speech. Only a reader can tell that apart from a fabrication,
# and a rule that guesses costs the reader words they never see. Fabrications
# are caught separately, by the rate and artifact guards built for them.

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
    """.split()
)


class Verdict(str, Enum):
    """What to do with a pair of readings."""

    SAME = "same"
    DIFFER = "differ"


def _is_content(token: str) -> bool:
    """A word that carries meaning, not grammar."""
    return len(token) > 1 and token not in _FUNCTION


def _only_repeats(primary: str, other: str) -> bool:
    """True when every difference is one side saying a phrase twice.

    "c'est pas encore, c'est pas encore parfait" against "c'est pas encore
    parfait" differs only by a repeat. A reader gains nothing from judging it,
    and marking it produces identical words either side of the bracket.
    """
    left, right = [_key(t) for t in primary.split()], [_key(t) for t in other.split()]
    if left == right:
        return False
    matcher = difflib.SequenceMatcher(None, left, right, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete" and _repeats(left, i1, i2):
            continue
        if tag == "insert" and _repeats(right, j1, j2):
            continue
        return False  # a replaced word is a real difference
    return True


def classify(primary: str, other: str) -> Verdict:
    """SAME only when nothing but grammar and filler moved."""
    if not compare(primary, other):
        return Verdict.SAME
    if _only_repeats(primary, other):
        return Verdict.SAME
    if all(
        _same_thing(d.primary.split(), d.other.split())
        for d in compare(primary, other)
    ):
        return Verdict.SAME
    left, right = normalise_tokens(primary), normalise_tokens(other)
    moved = [t for t in set(left) ^ set(right) if t]
    if moved and all(not _is_content(t) for t in moved):
        return Verdict.SAME
    return Verdict.DIFFER
