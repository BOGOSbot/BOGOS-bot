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
)


def fold(text: str) -> str:
    """Lowercase, accent-stripped, whitespace-collapsed."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.lower().split())


def is_artifact(text: str) -> bool:
    """True when the text is, or carries, a memorised subtitle credit."""
    folded = fold(text)
    if not folded:
        return False
    return any(phrase in folded for phrase in PHRASES)
