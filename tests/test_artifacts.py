"""The artifact list exists to catch memorised output, not real speech.

The negative cases matter more than the positive ones: a list that eats honest
transcripts is worse than no list, because the loss is silent and looks like
the speaker said nothing.
"""

from __future__ import annotations

import pytest

from stt import artifacts


@pytest.mark.parametrize(
    "text",
    [
        "Sous-titrage ST 501",
        "Sous-titrage ST' 501",
        "Sous-titrage FR ?",
        "Sous-titres realises par la communaute Amara.org",
        "Subtitles by the Amara.org community",
        "Thanks for watching!",
        "  SOUS-TITRAGE  ",
        "Sous titrage",
    ],
)
def test_memorised_credits_are_caught(text: str) -> None:
    assert artifacts.is_artifact(text)


@pytest.mark.parametrize(
    "text",
    [
        "Merci.",
        "il a dit merci beaucoup",
        "on joue contre les Necrons samedi",
        "Ah ouais, et en face il y a Fuegan, Asurmen et Eldrad Ulthran",
        "je vais faire une liste a partir de HQ",
        "",
    ],
)
def test_real_speech_is_left_alone(text: str) -> None:
    assert not artifacts.is_artifact(text)
