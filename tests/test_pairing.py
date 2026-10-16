"""The pairing reader: layout from the header, tabs that describe themselves.

Four rules were each got wrong once while building this, so each is pinned:
a player who fills only their Avec row is complete, the faction count follows
the event, a non-pairing tab is rejected rather than half-parsed, and a link
is accepted as readily as an id.
"""

from __future__ import annotations

import pytest

from bot import pairing

SHEET_ID = "0ExampleSheetIdForTests_1234567890abcdef"


def _header(factions: int = 6) -> list[str]:
    """The pairing header, including the trailing Liste column."""
    names = ["GI", "TS", "DG", "LoV", "Necron", "SM", "Tau", "CK"][:factions]
    head = ["Joueur", "Faction", *names, "", "", "Tables ?"]
    head += [""] * (19 - len(head))
    head.append("Liste")
    return head


def _row(head: list[str], name: str = "", matchups: int = 0,
         tables: str = "", liste: str = "") -> str:
    cells = [""] * len(head)
    cells[0] = name
    for index in range(2, 2 + matchups):
        cells[index] = "x"
    cells[head.index("Tables ?")] = tables
    cells[head.index("Liste")] = liste
    return ",".join(cells)


def _sheet(head: list[str], *rows: str) -> str:
    blank = ",".join([""] * len(head))
    return "\n".join([blank, ",".join(head), *rows]) + "\n"


def test_only_the_avec_row_filled_is_complete() -> None:
    head = _header()
    report = pairing.parse(_sheet(
        head,
        _row(head, "Alex", 6, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    ))
    assert report.players[0].avec == 6
    assert report.players[0].sans == 0
    assert report.players[0].done


def test_only_the_sans_row_filled_is_complete() -> None:
    head = _header()
    report = pairing.parse(_sheet(
        head,
        _row(head, "Zoé", 0, "Avec", "OK"),
        _row(head, "", 6, "Sans"),
    ))
    assert report.players[0].done


def test_a_partly_filled_row_is_not_complete() -> None:
    head = _header()
    player = pairing.parse(_sheet(
        head,
        _row(head, "Zed", 6, "Avec", ""),
        _row(head, "", 1, "Sans"),
    )).players[0]
    assert player.done is False
    assert player.liste_label == "Pas de liste"


def test_an_eight_faction_event_changes_the_target() -> None:
    head = _header(8)
    report = pairing.parse(_sheet(
        head,
        _row(head, "Dana", 8, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    ))
    assert report.matchups_total == 8
    assert report.players[0].done


def test_a_tab_without_a_liste_column_is_rejected() -> None:
    # Stats looks like a pairing tab until the missing Liste column is noticed.
    stats = "Joueur,Match,Victoire\nPierre,5,3\n"
    with pytest.raises(ValueError):
        pairing.parse(stats)


def test_an_annexe_style_tab_has_no_joueur_header() -> None:
    with pytest.raises(ValueError):
        pairing.parse("Club,Club,Yokai\nAlex,IK,DA\n")


def test_sheet_id_from_a_link_or_a_bare_id() -> None:
    link = "https://docs.google.com/spreadsheets/d/%s/edit?gid=1#gid=1" % SHEET_ID
    assert pairing.sheet_id_from(link) == SHEET_ID
    assert pairing.sheet_id_from(SHEET_ID) == SHEET_ID
    with pytest.raises(ValueError):
        pairing.sheet_id_from("https://example.com/not-a-sheet")


def test_the_summary_sections_by_team_and_pings_only_resolved_players() -> None:
    head = _header()
    yokai = pairing.parse(_sheet(
        head,
        _row(head, "Nils", 0, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
        _row(head, "Fab", 6, "Avec", ""),
        _row(head, "", 0, "Sans"),
    ))
    roster = pairing.Resolver(
        members=pairing.index_members([(4242, "Echo (Nils)", None, "ekvar")])
    )
    text = pairing.render([pairing.TabReport("Yokai", yokai)], roster)
    assert "**Yokai**" in text
    assert "<@4242>" in text
    assert "**Fab**" in text
    # The ignore list stays out of the message on purpose.
    assert "ignores" not in text


def test_the_summary_says_so_when_every_team_is_complete() -> None:
    head = _header()
    ok = pairing.parse(_sheet(
        head,
        _row(head, "Fab", 6, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    ))
    text = pairing.render([pairing.TabReport("Immat", ok)], pairing.Resolver())
    assert "Tout est bon." in text

# ---- name resolution ----------------------------------------------------

def _roster() -> pairing.Resolver:
    return pairing.Resolver(
        members=pairing.index_members([
            (1, "Nova (Alex)", "Alex", "kael38"),
            (2, "Lumen (Zoé)", None, "elyysm"),
            (3, "Zed (Sam)", "Zed", "mandal"),
            (4, "Echo (Nils)", None, "ekvar"),
        ])
    )


def test_normalize_ignores_case_and_accents() -> None:
    assert pairing.normalize("Zoé") == pairing.normalize("zoe")
    assert pairing.normalize("  Dana ") == "dana"


def test_a_nickname_splits_into_pseudo_and_prenom() -> None:
    assert pairing.split_nickname("Nova (Alex)") == ("Nova", "Alex")
    assert pairing.split_nickname("Grim-milo") == ("Grim-milo", "")


def test_the_roster_resolves_by_pseudo_or_by_prenom() -> None:
    roster = _roster()
    assert roster.resolve("Alex") == 1  # the prenom
    assert roster.resolve("Zoé") == 2  # accent-insensitive
    assert roster.resolve("Zed") == 3  # the pseudo, not Sam
    assert roster.resolve("Nils") == 4


def test_an_ambiguous_name_is_never_guessed() -> None:
    roster = pairing.Resolver(
        members=pairing.index_members([
            (1, "A (Nils)", None, "a"),
            (2, "B (Nils)", None, "b"),
        ])
    )
    assert roster.resolve("Nils") is None


def test_a_unique_prefix_resolves_and_an_ambiguous_one_does_not() -> None:
    one = pairing.Resolver(
        members=pairing.index_members([(1, "Pixel (Fabien)", None, "canapelit")])
    )
    assert one.resolve("Fab") == 1
    two = pairing.Resolver(
        members=pairing.index_members([
            (1, "A (Fabien)", None, "a"),
            (2, "B (Fabrice)", None, "b"),
        ])
    )
    assert two.resolve("Fab") is None


def test_a_name_the_roster_cannot_settle_is_left_unpinged() -> None:
    # Milo has no counterpart in the guild. A bold name is the honest output,
    # and is preferred over any guess: this project carries no id to fall back on.
    roster = _roster()
    assert roster.resolve("Milo") is None
    head = _header()
    report = pairing.parse(_sheet(
        head,
        _row(head, "Milo", 0, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    ))
    text = pairing.render([pairing.TabReport("Yokai", report)], roster)
    assert "**Milo**" in text
    assert "<@" not in text

def test_a_full_nickname_in_the_sheet_still_matches() -> None:
    # The sheet may carry the whole 
    # nickname rather than one half of it, in whatever spacing.
    roster = pairing.Resolver(
        members=pairing.index_members([(1, "Zed (Sam)", "Zed", "mandal")])
    )
    for probe in (
        "Zed (Sam)",
        "Zed(Sam)",
        "Zed ( Sam )",
        "zed(sam)",
    ):
        assert roster.resolve(probe) == 1, probe


def test_normalize_ignores_spacing_around_parentheses() -> None:
    assert pairing.normalize("Zed (Sam)") == pairing.normalize("Zed(Sam)")
    assert pairing.normalize("Zed (Sam)") == pairing.normalize("Zed ( Sam )")
