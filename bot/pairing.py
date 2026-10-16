"""Read a BOGOS pairing workbook and report who has filled what.

One workbook per tournament, one tab per opposing team. The tab label IS the
team name, so a report is produced per tab.

Nothing here needs Google credentials: a link-shared sheet exposes an xlsx
export (the only credential-free way to enumerate tabs) and a gviz CSV per
tab. That keeps the feature working before the bot is hosted, with nothing
to expire and no service account to leak.

Which tabs count is decided by the tabs themselves rather than by a list: a
pairing tab has a Joueur header row, a Liste column and at least one player.
That selects the team tabs and skips the others - Stats has no Liste column,
Annexe has no Joueur row, Reference is an empty template, Fiches is blank -
and a new team tab is picked up with no configuration.

Layout it depends on, read from the header row rather than hardcoded:
  Joueur | Faction | <opponent factions...> | Tables ? | ... | Liste

A player fills the matchup columns in EITHER the Avec row or the Sans row -
they pick tables or they do not - and must fill all of them. So the test is
max(avec, sans) == number of faction columns, which keeps working when an
event has 8 factions instead of 6.
"""

from __future__ import annotations

import csv
import io
import re
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from xml.etree import ElementTree

from utils.logging import get_logger

logger = get_logger(__name__)

_SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
_BARE_ID = re.compile(r"^[A-Za-z0-9_-]{20,}$")
_XLSX_URL = "https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"
_GVIZ_URL = (
    "https://docs.google.com/spreadsheets/d/{sid}/gviz/tq?tqx=out:csv&sheet={name}"
)
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@dataclass
class Player:
    """One row of a pairing tab, reduced to what the summary needs."""

    name: str
    faction: str
    avec: int
    sans: int
    matchups_total: int
    liste: str

    @property
    def matchups_done(self) -> bool:
        return max(self.avec, self.sans) >= self.matchups_total

    @property
    def liste_ok(self) -> bool:
        return self.liste.strip().upper() == "OK"

    @property
    def liste_label(self) -> str:
        """What to show when the list is missing: the cell, or a plain note."""
        return self.liste.strip() or "Pas de liste"

    @property
    def done(self) -> bool:
        return self.matchups_done and self.liste_ok


@dataclass
class Report:
    players: list[Player] = field(default_factory=list)
    matchups_total: int = 0

    @property
    def matchups_missing(self) -> list[Player]:
        return [p for p in self.players if not p.matchups_done]

    @property
    def liste_missing(self) -> list[Player]:
        return [p for p in self.players if not p.liste_ok]


@dataclass
class TabReport:
    """One team tab: the label is the team name."""

    tab: str
    report: Report


def _get(url: str, timeout: float = 60.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "BOGOS-bot/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def sheet_id_from(value: str) -> str:
    """Accept a full Sheets link or a bare id, since links get pasted."""
    value = (value or "").strip()
    match = _SHEET_ID.search(value)
    if match:
        return match.group(1)
    if _BARE_ID.match(value):
        return value
    raise ValueError(f"not a Google Sheets link or id: {value!r}")


def list_tabs(sheet_id: str, timeout: float = 60.0) -> list[str]:
    """Tab labels in workbook order.

    The xlsx export is the only credential-free route that enumerates tabs;
    the per-tab CSV export needs a gid we would otherwise have to guess.
    """
    blob = _get(_XLSX_URL.format(sid=sheet_id), timeout)
    with zipfile.ZipFile(io.BytesIO(blob)) as book:
        workbook = ElementTree.fromstring(book.read("xl/workbook.xml"))
    return [sheet.attrib["name"] for sheet in workbook.iter(_NS + "sheet")]


def fetch_tab_csv(sheet_id: str, tab: str, timeout: float = 60.0) -> str:
    """One tab as CSV, by label rather than by gid."""
    url = _GVIZ_URL.format(sid=sheet_id, name=urllib.parse.quote(tab))
    return _get(url, timeout).decode("utf-8", errors="replace")


def _filled(row: list[str], columns: list[int]) -> int:
    return sum(1 for i in columns if i < len(row) and row[i].strip())


def parse(csv_text: str) -> Report:
    """Parse a pairing tab, or raise if this tab is not one.

    Raising is how non-pairing tabs are filtered: Stats has a Joueur header
    but no Liste column, Annexe has no Joueur header at all.
    """
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_index = next(
        (i for i, row in enumerate(rows) if row and row[0].strip() == "Joueur"),
        None,
    )
    if header_index is None:
        raise ValueError("no Joueur header row")

    header = [(cell or "").strip() for cell in rows[header_index]]
    # Faction columns start at 2 and run until the first blank header, so the
    # count follows the event (6 or 8) instead of being hardcoded.
    end = 2
    while end < len(header) and header[end]:
        end += 1
    faction_columns = list(range(2, end))
    liste_column = next(
        (i for i, h in enumerate(header) if h.lower().startswith("liste")),
        None,
    )
    if liste_column is None or not faction_columns:
        raise ValueError("not a pairing tab: no Liste column or no factions")

    table_index = next(
        (
            i
            for i in range(header_index + 1, len(rows))
            if rows[i] and rows[i][0].strip() == "Table"
        ),
        len(rows),
    )

    report = Report(matchups_total=len(faction_columns))
    for index in range(header_index + 1, table_index):
        row = rows[index]
        name = (row[0] if row else "").strip()
        if not name:
            continue
        # The Sans row sits directly under the player row, with no name of its own.
        below = rows[index + 1] if index + 1 < len(rows) else []
        sans_row = below if below and not below[0].strip() else None
        report.players.append(
            Player(
                name=name,
                faction=(row[1] if len(row) > 1 else "").strip(),
                avec=_filled(row, faction_columns),
                sans=_filled(sans_row, faction_columns) if sans_row else 0,
                matchups_total=len(faction_columns),
                liste=(row[liste_column] if len(row) > liste_column else ""),
            )
        )
    return report


def collect(sheet_id: str) -> tuple[list[TabReport], list[str]]:
    """Every tab that parses as a pairing table, plus the tabs skipped.

    The skipped list is returned for logging rather than for display.
    """
    found: list[TabReport] = []
    skipped: list[str] = []
    tabs = list_tabs(sheet_id)
    for tab in tabs:
        try:
            report = parse(fetch_tab_csv(sheet_id, tab))
        except Exception as exc:
            logger.debug("Skipping tab %r: %s", tab, exc)
            skipped.append(tab)
            continue
        if not report.players:
            # An empty template still parses; it just has nobody in it.
            skipped.append(tab)
            continue
        found.append(TabReport(tab, report))
    return found, skipped


def _who(player: Player, mentions: dict[str, int]) -> str:
    """A real ping when mapped: Discord renders it as the nickname."""
    user_id = mentions.get(player.name.strip())
    if user_id:
        return f"<@{user_id}> ({player.name.strip()})"
    return f"**{player.name.strip()}**"


def render(
    tabs: list[TabReport],
    mentions: dict[str, int],
    limit: int = 1900,
) -> str:
    """One section per team, so a ping is always attributable to a matchup.

    Skipped tabs are deliberately not listed: a team tab going missing is a
    thing the group notices from the result itself, so printing the ignore list
    only adds noise to every well-behaved run. It is logged instead.
    """
    if not tabs:
        return "Aucun onglet de pairing trouve dans cette feuille."

    lines = [f"**Pairing** - {len(tabs)} equipe(s) : " + ", ".join(t.tab for t in tabs)]
    for entry in tabs:
        lines.append("")
        lines.append(f"**{entry.tab}**")
        missing_matchups = entry.report.matchups_missing
        missing_liste = entry.report.liste_missing
        if not missing_matchups and not missing_liste:
            lines.append("Tout est bon.")
            continue
        for player in missing_matchups:
            filled = max(player.avec, player.sans)
            lines.append(
                f"- {_who(player, mentions)} : matchups {filled}/{entry.report.matchups_total}"
            )
        for player in missing_liste:
            lines.append(f"- {_who(player, mentions)} : liste - {player.liste_label}")

    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text
