"""The pairing parser reads its layout from the header, not from constants.

The two rules worth pinning down are the ones that were got wrong first: a
player who fills only their Avec row is done, and the faction count follows
the event instead of being 6.
"""

from __future__ import annotations

from bot import pairing


def _header(factions: int = 6) -> list[str]:
    """The sheet's header, with the trailing columns the summary needs."""
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
    text = _sheet(head,
        _row(head, "Martial", 6, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    )
    report = pairing.parse(text)
    assert len(report.players) == 1
    assert report.players[0].avec == 6
    assert report.players[0].sans == 0
    assert report.players[0].done


def test_only_the_sans_row_filled_is_complete() -> None:
    head = _header()
    text = _sheet(head,
        _row(head, "Lea", 0, "Avec", "OK"),
        _row(head, "", 6, "Sans"),
    )
    assert pairing.parse(text).players[0].done


def test_a_partly_filled_row_is_not_complete() -> None:
    head = _header()
    text = _sheet(head,
        _row(head, "Mandal", 6, "Avec", ""),
        _row(head, "", 1, "Sans"),
    )
    player = pairing.parse(text).players[0]
    assert player.done is False
    assert player.liste_label == "Pas de liste"


def test_an_eight_faction_event_changes_the_target() -> None:
    head = _header(8)
    text = _sheet(head,
        _row(head, "Rony", 8, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    )
    report = pairing.parse(text)
    assert report.matchups_total == 8
    assert report.players[0].done


def test_the_summary_pings_a_mapped_player_and_plainly_names_the_rest() -> None:
    head = _header()
    text = _sheet(head,
        _row(head, "Nathan", 0, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
        _row(head, "Fab", 6, "Avec", "OK"),
        _row(head, "", 0, "Sans"),
    )
    rendered = pairing.render(pairing.parse(text), {"Nathan": 4242})
    assert "<@4242>" in rendered
    assert "Nathan" not in rendered.replace("<@4242>", "")
    assert "Tout est bon." not in rendered
