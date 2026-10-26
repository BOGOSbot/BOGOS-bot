"""The clean copy and its twin: what the readers of the 26 Oct meeting cut.

Every line they quoted as a hallucination must go to the second file, and a
short line that names something the meeting is about must stay.
"""

from __future__ import annotations

import pytest

from bot import triage

TERMS = ["Orks", "Disruption", "Take and Hold", "Cyril", "Necrons"]


def _entry(text: str) -> dict:
    return {"name": "X", "text": text, "line": f"**X** (21:00:00) : {text}"}


@pytest.mark.parametrize(
    "text",
    ["Merci.", "What?", "Mm-hmm.", "Yeah.", "Ah, je suis...", "Oui.", ".",
     "Ha ha ha.", "Where, where, where?", "Guillaume, tu joues le..."],
)
def test_what_the_readers_cut_is_cut(text):
    kept, cut = triage.split([_entry(text)], TERMS)
    assert not kept and len(cut) == 1


@pytest.mark.parametrize(
    "text",
    ["Orks.", "En disruption.", "Merci Cyril.",
     "Et c'est quand la deadline, du coup ?",
     "On joue contre les Necrons samedi, donc il faut une liste solide."],
)
def test_content_stays(text):
    kept, cut = triage.split([_entry(text)], TERMS)
    assert len(kept) == 1 and not cut


def test_a_long_turn_that_trails_off_stays():
    text = "Donc la procedure de remise de liste a change parce qu'en fait..."
    kept, _ = triage.split([_entry(text)], [])
    assert kept


def test_order_and_entries_are_untouched():
    entries = [_entry("Oui."), _entry("On va jouer Take and Hold ce soir."), _entry("Hmm.")]
    kept, cut = triage.split(entries, TERMS)
    assert [e["text"] for e in kept] == ["On va jouer Take and Hold ce soir."]
    assert [e["text"] for e in cut] == ["Oui.", "Hmm."]
    assert kept[0] is entries[1]
