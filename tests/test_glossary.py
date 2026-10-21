"""The glossary: two plain lists, and the one line that puts them in the prompt."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

import pytest

from stt import glossary


@pytest.fixture
def lists(tmp_path, monkeypatch):
    """A shipped list and a local overlay of our own, both by absolute path."""
    shipped = tmp_path / "glossary.txt"
    local = tmp_path / "glossary.local.txt"
    session = tmp_path / "glossary.session.txt"
    monkeypatch.setattr(
        glossary,
        "stt_cfg",
        SimpleNamespace(glossary_file=str(shipped), glossary_local_file=str(local)),
    )
    monkeypatch.setattr(glossary, "session_path", lambda: session)
    return shipped, local, session


def test_blank_lines_and_comments_are_not_terms(lists):
    shipped, _, _ = lists
    shipped.write_text("# a note\n\nAeldari\n   \n# another\nDrukhari\n", encoding="utf-8")
    assert glossary.terms() == ["Aeldari", "Drukhari"]


def test_a_repeated_term_keeps_the_first_spelling(lists):
    shipped, _, _ = lists
    shipped.write_text("Aeldari\nAELDARI\naeldari\nNecrons\n", encoding="utf-8")
    assert glossary.terms() == ["Aeldari", "Necrons"]


def test_a_byte_order_mark_does_not_ride_into_the_first_term(lists):
    """Windows editors add one, and it would be sent to the model as text."""
    shipped, _, _ = lists
    shipped.write_bytes("\ufeffAeldari\n".encode("utf-8"))
    assert glossary.terms() == ["Aeldari"]


def test_the_local_list_is_read_after_the_shipped_one(lists):
    """The team's own armies and players do not belong in a public repository."""
    shipped, local, _ = lists
    shipped.write_text("Aeldari\nDrukhari\n", encoding="utf-8")
    local.write_text("Mandal\nLe Gite du Lac\n", encoding="utf-8")
    assert glossary.terms() == ["Aeldari", "Drukhari", "Mandal", "Le Gite du Lac"]


def test_the_shipped_spelling_wins_a_duplicate(lists):
    shipped, local, _ = lists
    shipped.write_text("Aeldari\n", encoding="utf-8")
    local.write_text("AELDARI\nNecrons\n", encoding="utf-8")
    assert glossary.terms() == ["Aeldari", "Necrons"]


def test_no_local_list_is_fine(lists):
    shipped, _, _ = lists
    shipped.write_text("Aeldari\n", encoding="utf-8")
    assert glossary.terms() == ["Aeldari"]


def test_no_file_is_no_vocabulary(lists):
    assert glossary.terms() == []
    assert glossary.append_to("base") == "base"


def test_an_empty_setting_turns_it_off(monkeypatch):
    monkeypatch.setattr(
        glossary,
        "stt_cfg",
        SimpleNamespace(glossary_file="", glossary_local_file=""),
    )
    # The session list is always a path; it simply does not exist.
    assert glossary.paths() == [glossary.session_path()]
    assert glossary.terms() == []
    assert glossary.append_to("base") == "base"


def test_a_relative_name_resolves_against_the_project_root(monkeypatch):
    monkeypatch.setattr(
        glossary,
        "stt_cfg",
        SimpleNamespace(glossary_file="glossary.txt", glossary_local_file=""),
    )
    assert glossary.paths() == [glossary.ROOT / "glossary.txt", glossary.session_path()]


def test_the_terms_reach_the_prompt_and_only_if_heard(lists):
    shipped, _, _ = lists
    shipped.write_text("Aeldari\nDrukhari\n", encoding="utf-8")
    prompt = glossary.append_to("Transcribe this audio.")

    assert prompt.startswith("Transcribe this audio.")
    assert "Aeldari, Drukhari" in prompt
    # The line must not read as an instruction to use them.
    assert "if you hear them" in prompt


def test_a_glossary_that_grows_past_the_core_warns_once(lists, caplog):
    shipped, _, _ = lists
    shipped.write_text("\n".join("Term%d" % n for n in range(1200)), encoding="utf-8")
    glossary._warned = False

    with caplog.at_level("WARNING"):
        glossary.append_to("base")
        glossary.append_to("base")

    assert sum("Consider scoping it" in r.message for r in caplog.records) == 1


def test_the_shipped_glossary_is_a_core_vocabulary():
    """The list that travels with the repo, not a corpus dump."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        glossary,
        "stt_cfg",
        SimpleNamespace(
            glossary_file=str(glossary.ROOT / "glossary.txt"), glossary_local_file=""
        ),
    )
    found = glossary.terms()
    monkeypatch.undo()

    assert len(found) > 50, "the shipped list is suspiciously thin"
    assert sum(len(t) for t in found) < glossary.WARN_CHARS
    for term in ("Warhammer 40,000", "Aeldari", "Eldar", "Drukhari", "Necrons", "pairing"):
        assert term in found


def test_the_pairing_sheet_names_join_the_list(lists):
    """What the bot learns at !transcript, and nobody has to type."""
    shipped, _, session = lists
    shipped.write_text("Aeldari\n", encoding="utf-8")

    published = glossary.set_session_terms(["Les Chevaliers", "Mandal", "Necrons"])

    assert published == 3
    assert session.is_file()
    assert glossary.terms() == ["Aeldari", "Les Chevaliers", "Mandal", "Necrons"]


def test_the_shipped_spelling_still_wins_a_sheet_name(lists):
    shipped, _, _ = lists
    shipped.write_text("Necrons\n", encoding="utf-8")
    glossary.set_session_terms(["NECRONS", "Mandal"])
    assert glossary.terms() == ["Necrons", "Mandal"]


def test_closing_the_session_forgets_the_sheet(lists):
    """A name from one tournament must not colour the next."""
    shipped, _, session = lists
    shipped.write_text("Aeldari\n", encoding="utf-8")
    glossary.set_session_terms(["Mandal"])

    assert glossary.set_session_terms([]) == 0

    assert not session.exists()
    assert glossary.terms() == ["Aeldari"]


def test_a_cell_too_short_to_be_a_name_is_dropped(lists):
    assert glossary.set_session_terms(["OK", "-", "", "  ", "Mandal"]) == 1


# -- what a new session may adopt ---------------------------------------------
# The pairing sheet is a format: a new one is made for every meeting, so its
# names only exist behind the link and !pairing is how most of them get read.


def test_a_session_list_from_this_meeting_is_adopted(lists):
    """!pairing reads the sheet, !transcript picks the names up."""
    glossary.set_session_terms(["Les Chevaliers", "Mandal", "Necrons"])
    assert glossary.session_terms() == ["Les Chevaliers", "Mandal", "Necrons"]


def test_a_session_list_nobody_transcribed_is_not_adopted(lists):
    """A name from the wrong tournament is the exact harm a glossary prevents."""
    _, _, session = lists
    glossary.set_session_terms(["Les Chevaliers", "Mandal"])
    stale = time.time() - glossary.SESSION_MAX_AGE_SECONDS - 60
    os.utime(session, (stale, stale))
    assert glossary.session_terms() == []


def test_adopting_is_gated_but_reading_is_not(lists):
    """A meeting that has started keeps its names however long it runs."""
    _, _, session = lists
    glossary.set_session_terms(["Mandal"])
    stale = time.time() - glossary.SESSION_MAX_AGE_SECONDS - 60
    os.utime(session, (stale, stale))
    assert glossary.session_terms() == [], "not adopted"
    assert glossary.terms() == ["Mandal"], "and still read once running"


def test_no_session_list_is_no_names(lists):
    assert glossary.session_terms() == []


def test_a_comment_in_the_session_list_is_not_a_name(lists):
    _, _, session = lists
    session.write_text("# written by !pairing\nMandal\n", encoding="utf-8")
    assert glossary.session_terms() == ["Mandal"]
