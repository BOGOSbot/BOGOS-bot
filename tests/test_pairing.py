"""The pairing reader: layout from the header, tabs that describe themselves.

Four rules were each got wrong once while building this, so each is pinned:
a player who fills only their Avec row is complete, the faction count follows
the event, a non-pairing tab is rejected rather than half-parsed, and a link
is accepted as readily as an id.
"""

from __future__ import annotations

import pytest

from bot import pairing

SHEET_ID = "1ctr4d8g6YcgMBbOtprJUpH4MEdFU6dIig9KBJn8hp5A"


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
        _row(head, "Martial", 6, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    ))
    assert report.players[0].avec == 6
    assert report.players[0].sans == 0
    assert report.players[0].done


def test_only_the_sans_row_filled_is_complete() -> None:
    head = _header()
    report = pairing.parse(_sheet(
        head,
        _row(head, "Lea", 0, "Avec", "OK"),
        _row(head, "", 6, "Sans"),
    ))
    assert report.players[0].done


def test_a_partly_filled_row_is_not_complete() -> None:
    head = _header()
    player = pairing.parse(_sheet(
        head,
        _row(head, "Mandal", 6, "Avec", ""),
        _row(head, "", 1, "Sans"),
    )).players[0]
    assert player.done is False
    assert player.liste_label == "Pas de liste"


def test_an_eight_faction_event_changes_the_target() -> None:
    head = _header(8)
    report = pairing.parse(_sheet(
        head,
        _row(head, "Rony", 8, "Avec", "OK"),
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
        pairing.parse("BOGOS,BOGOS,Yokai\nMartial,IK,DA\n")


def test_sheet_id_from_a_link_or_a_bare_id() -> None:
    link = "https://docs.google.com/spreadsheets/d/%s/edit?gid=1#gid=1" % SHEET_ID
    assert pairing.sheet_id_from(link) == SHEET_ID
    assert pairing.sheet_id_from(SHEET_ID) == SHEET_ID
    with pytest.raises(ValueError):
        pairing.sheet_id_from("https://example.com/not-a-sheet")


def test_the_summary_sections_by_team_and_pings_only_mapped_players() -> None:
    head = _header()
    yokai = pairing.parse(_sheet(
        head,
        _row(head, "Nathan", 0, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
        _row(head, "Fab", 6, "Avec", ""),
        _row(head, "", 0, "Sans"),
    ))
    text = pairing.render([pairing.TabReport("Yokai", yokai)], {"Nathan": 4242})
    assert "**Yokai**" in text
    assert "<@4242> (Nathan)" in text
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
    text = pairing.render([pairing.TabReport("Immat", ok)], {})
    assert "Tout est bon." in text
