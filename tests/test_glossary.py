"""The glossary: a plain list, and the one line that puts it in the prompt."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from stt import glossary


@pytest.fixture
def listfile(tmp_path, monkeypatch):
    """A glossary of our own, pointed at by an absolute path."""
    path = tmp_path / "glossary.txt"
    monkeypatch.setattr(glossary, "stt_cfg", SimpleNamespace(glossary_file=str(path)))
    return path


def test_blank_lines_and_comments_are_not_terms(listfile):
    listfile.write_text(
        "# a note\n\nAeldari\n   \n# another note\nDrukhari\n", encoding="utf-8"
    )
    assert glossary.terms() == ["Aeldari", "Drukhari"]


def test_a_repeated_term_keeps_the_first_spelling(listfile):
    listfile.write_text("Aeldari\nAELDARI\naeldari\nNecrons\n", encoding="utf-8")
    assert glossary.terms() == ["Aeldari", "Necrons"]


def test_a_byte_order_mark_does_not_ride_into_the_first_term(listfile):
    """Windows editors add one, and it would be sent to the model as text."""
    listfile.write_bytes("\ufeffAeldari\n".encode("utf-8"))
    assert glossary.terms() == ["Aeldari"]


def test_no_file_is_no_vocabulary(listfile):
    assert glossary.terms() == []
    assert glossary.append_to("base") == "base"


def test_an_empty_setting_turns_it_off(monkeypatch):
    monkeypatch.setattr(glossary, "stt_cfg", SimpleNamespace(glossary_file=""))
    assert glossary.path() is None
    assert glossary.append_to("base") == "base"


def test_a_relative_name_resolves_against_the_project_root(monkeypatch):
    monkeypatch.setattr(glossary, "stt_cfg", SimpleNamespace(glossary_file="glossary.txt"))
    assert glossary.path() == glossary.ROOT / "glossary.txt"


def test_the_terms_reach_the_prompt_and_only_if_heard(listfile):
    listfile.write_text("Aeldari\nDrukhari\n", encoding="utf-8")
    prompt = glossary.append_to("Transcribe this audio.")

    assert prompt.startswith("Transcribe this audio.")
    assert "Aeldari, Drukhari" in prompt
    # The line must not read as an instruction to use them.
    assert "if you hear them" in prompt


def test_a_glossary_that_grows_past_the_core_warns_once(listfile, caplog):
    listfile.write_text("\n".join("Term%d" % n for n in range(1200)), encoding="utf-8")
    glossary._warned = False

    with caplog.at_level("WARNING"):
        glossary.append_to("base")
        glossary.append_to("base")

    assert sum("Consider scoping it" in r.message for r in caplog.records) == 1


def test_the_shipped_glossary_is_a_core_vocabulary():
    """The list that travels with the repo, not a corpus dump."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        glossary, "stt_cfg", SimpleNamespace(glossary_file=str(glossary.ROOT / "glossary.txt"))
    )
    found = glossary.terms()
    monkeypatch.undo()

    assert len(found) > 50, "the shipped list is suspiciously thin"
    assert sum(len(t) for t in found) < glossary.WARN_CHARS
    for term in ("Warhammer 40,000", "Aeldari", "Eldar", "Drukhari", "Necrons"):
        assert term in found
