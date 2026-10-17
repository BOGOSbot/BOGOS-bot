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


def test_render_marks_the_words_that_moved():
    """The reader sees which word is contested, in place, once."""
    assert conflicts.render("il est pistol", "il est pistolet") == "il est (pistol | pistolet)"


def test_render_of_the_hard_one_from_the_session():
    line = conflicts.render(
        "Ça me va. Ça se rate. Sophie, tu fais douze, sale bâtard.",
        "Ça me va. Ça se rate. Sauf si tu fais 12. Sale bâtard.",
    )
    assert line == "Ça me va. Ça se rate. (Sophie, | Sauf si) tu fais (douze | 12), sale bâtard."


def test_render_treats_punctuation_as_no_difference():
    """A comma against a full stop is not something to judge."""
    assert conflicts.render("on passe à cinq, ok", "on passe à 5. ok") == "on passe à (cinq | 5), ok"


def test_render_marks_an_inserted_word():
    assert conflicts.render("Je pense pas", "Je ne pense pas") == "Je (∅ | ne) pense pas"


def test_render_leaves_an_agreement_alone():
    assert conflicts.render("hop il revient à deux", "hop il revient à deux") == "hop il revient à deux"


def test_a_stylistic_difference_is_not_a_conflict():
    """Two of the pairs a reader labelled false positives: keep either."""
    a = conflicts.classify("Je pense pas te le reprendre.", "Je ne pense pas te le reprendre.")
    b = conflicts.classify("Hmm, peut-être un peu la merde, ça.", "Hum, peut-être un peu la merde, ça.")
    assert a is conflicts.Verdict.SAME
    assert b is conflicts.Verdict.SAME


def test_a_content_word_moving_is_a_conflict():
    assert conflicts.classify("les nécrons c'est bien", "les écrans c'est bien") is conflicts.Verdict.DIFFER


def test_nothing_in_common_is_still_shown_not_cut():
    """One of these is real speech, and only a reader can tell which."""
    a = conflicts.classify("J'ai remis mes points.", "Jean-Rémy Dupont.")
    b = conflicts.classify("Carré neuf.", "Jeune homme.")
    assert a is conflicts.Verdict.DIFFER
    assert b is conflicts.Verdict.DIFFER


def test_no_verdict_drops_a_reading():
    """A rule that guesses which side is wrong costs words nobody sees."""
    assert {v.value for v in conflicts.Verdict} == {"same", "differ"}
