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


def test_encyclopedia_fabrications_are_recognised():
    """Measured: 14 of these in one 15-minute French session."""
    from stt import artifacts
    invented = (
        "Le 1er janvier 2010, le gouvernement de la province de l'Ontario a "
        "annoncé qu'il allait fermer le centre de détention de Toronto."
    )
    assert artifacts.is_artifact(invented)
    assert artifacts.is_artifact("Le 1er janvier 1992, le président de la République, François Mitterrand, a inauguré le musée d'Orsay.")


def test_real_speech_is_not_an_artifact():
    for text in ("J'ai remis mes points.", "Carré neuf.", "Allez !", "Merci."):
        assert not artifacts.is_artifact(text)
