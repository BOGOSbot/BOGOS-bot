"""Two models, one reader: what actually counts as a conflict."""

from __future__ import annotations

from stt import conflicts


def test_digits_and_words_are_the_same_thing():
    """About 20 of the 55 raw differences were this. Not a conflict."""
    assert conflicts.compare("trois tirs", "3 tirs") == []


def test_accents_case_and_punctuation_do_not_matter():
    assert conflicts.compare("Les Nécrons arrivent.", "les necrons arrivent!") == []


def test_noise_around_a_number_is_not_a_conflict():
    assert conflicts.compare("on passe à cinq", "on passe à 5") == []


def test_the_real_dice_case():
    """gpt wrote "six puces"; voxtral read the notation as it was said."""
    found = conflicts.compare("Des six puces.", "Des 6+.")
    assert len(found) == 1
    assert found[0].primary == "puces."
    assert found[0].other == ""


def test_a_proper_noun_swap_is_the_conflict_that_matters():
    found = conflicts.compare("les nécrons c'est bien", "les écrans c'est bien")
    assert len(found) == 1
    assert found[0].primary == "nécrons"
    assert found[0].other == "écrans"


def test_a_fabricated_phrase_is_reported():
    found = conflicts.compare(
        "c'est bon circumcil", "louvre egan pour le drathil pan j ai mon circuncil"
    )
    assert found


def test_identical_text_has_no_conflicts():
    assert conflicts.compare("hop il revient à deux", "hop il revient à deux") == []


def test_divergence_separates_word_choice_from_a_rebuilt_sentence():
    """The threshold the display switches on, measured on real lines."""
    small = conflicts.divergence("les nécrons c'est bien non", "les écrans c'est bien non")
    large = conflicts.divergence(
        "Mais merde, ils vont bourrer. Olivier, attends, je tiens.",
        "Mais mort de rire, il est bourré. Olivier, ton chétien.",
    )
    assert small < 0.25
    assert large > 0.25


def test_describe_lists_the_spans_a_reader_judges():
    found = conflicts.compare("il est pistol", "il est pistolet")
    assert conflicts.describe(found) == "pistol → pistolet"


def test_describe_caps_a_long_list():
    """Two separate edits, so the cap actually applies."""
    found = conflicts.compare(
        "un chien deux chats trois loups quatre renards",
        "un X deux chats trois Y quatre renards",
    )
    assert len(found) == 2
    assert "+1 autre(s)" in conflicts.describe(found, cap=1)
