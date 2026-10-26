"""Phrases an ASR model emits from memory rather than from the audio.

Whisper-family decoders answer silence and noise with memorised subtitle
credits. Measured on this project's own dumps, 1.6-1.9s of near silence came
back as "Sous-titrage ST 501" and "Sous-titrage FR ?" - and that was the
model's *most* confident output of the batch: avg_logprob -0.206, the highest
recorded, with individual words at p=0.997, and no_speech_prob 0.000. So
confidence cannot catch this and a list can: the phrase is memorised, not
heard.

Only phrases that are never conversational belong here. "Merci" is real
speech - one dump of 0.67s returned exactly that - so blocking it would delete
honest transcripts to catch a hallucination. Extend this list from evidence,
not from imagination.
"""

from __future__ import annotations

import re
import unicodedata

# Matched against the folded text as substrings, because a hallucination often
# carries a suffix: "Sous-titrage ST 501", "Subtitles by the Amara.org
# community".
PHRASES: tuple[str, ...] = (
    "sous-titrage",
    "sous titrage",
    "sous-titres",
    "sous titres",
    "amara.org",
    "subtitles by",
    "subtitled by",
    "thanks for watching",
    "thank you for watching",
    "abonnez-vous",
    "abonnez vous",
    # Memorised encyclopedia prose. Measured: 14 utterances in one 15-minute
    # French session, nearly all opening with the same Wikipedia formula, on
    # clips of 0.2 to 1.6 seconds. The plausibility guard rejects these by rate
    # alone; this list is the belt to that pair of braces.
    "1er janvier",
    "le gouvernement a annonce",
    "le gouvernement de la",
    "guerre froide",
    "musee d'orsay",
    "francois mitterrand",
    "prix hugo",
    "jacques chirac",
    "union sovietique",
    "mvp de la saison",
    "parc national",
)


# Whole readings that are never what this table said. Matched against the entire
# reading, with its punctuation taken off, never as a substring. Measured on the
# 26 Oct meeting: "Thank you." came back 49 times, from 0.5 to 15 seconds of
# room noise, in a French conversation. "Merci", "Oui" and "OK" stay out: they
# are said for real at this table.
WHOLE: frozenset[str] = frozenset({
    "thank you",
    "thank you very much",
    "thanks",
})


def fold(text: str) -> str:
    """Lowercase, accent-stripped, whitespace-collapsed."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


def is_artifact(text: str) -> bool:
    """True when the text is a memorised phrase, or no words at all.

    A reading of nothing but punctuation - ".", "...", "," - is the model saying
    it heard no speech; 173 lines of the 26 Oct transcript were exactly that.
    """
    folded = fold(text)
    if not folded:
        return False
    bare = " ".join(re.sub(r"[^\w']+", " ", folded).split())
    if not bare:
        return True
    if bare in WHOLE:
        return True
    return any(phrase in folded for phrase in PHRASES)
