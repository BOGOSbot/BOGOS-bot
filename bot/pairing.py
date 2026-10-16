"""Read the BOGOS pairing sheet and report who has filled what.

The sheet is shared link-readable, so this needs no Google credentials: the
CSV export is a plain GET. That is why the feature works before anything is
hosted, and why there is no service account to leak or expire.

Layout it depends on, read from the header row rather than hardcoded:
  Joueur | Faction | <factions...> | Tables ? | ... | Liste

A player fills the matchup columns in EITHER the Avec row or the Sans row -
they pick tables or they do not - and must fill all of them. So the test is
max(avec, sans) == number of faction columns, which keeps working when an
event has 8 factions instead of 6.
"""

from __future__ import annotations

import csv
import io
import urllib.request
from dataclasses import dataclass, field

from utils.logging import get_logger

logger = get_logger(__name__)

_CSV_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


@dataclass
class Player:
    """One row of the pairing sheet, reduced to what the summary needs."""

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


def _csv_url(sheet_id: str, gid: str) -> str:
    return _CSV_URL.format(sheet_id=sheet_id, gid=gid)


def fetch_csv(sheet_id: str, gid: str, timeout: float = 30.0) -> str:
    """Download the tab as CSV. No credentials: the sheet is link-readable."""
    url = _csv_url(sheet_id, gid)
    request = urllib.request.Request(url, headers={"User-Agent": "BOGOS-bot/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _filled(row: list[str], columns: list[int]) -> int:
    return sum(1 for i in columns if i < len(row) and row[i].strip())


def parse(csv_text: str) -> Report:
    """Turn the CSV into a Report, reading the layout from the header row."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    header_index = next(
        (i for i, row in enumerate(rows) if row and row[0].strip() == "Joueur"),
        None,
    )
    if header_index is None:
        raise ValueError("no Joueur header row found in the sheet")

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
    if liste_column is None:
        raise ValueError("no Liste column found in the sheet")

    table_index = next(
        (i for i in range(header_index + 1, len(rows)) if rows[i] and rows[i][0].strip() == "Table"),
        len(rows),
    )

    report = Report(matchups_total=len(faction_columns))
    for index in range(header_index + 1, table_index):
        row = rows[index]
        name = (row[0] if row else "").strip()
        if not name:
            continue
        # The Sans row sits directly under the player row with no name of its own.
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


def render(report: Report, mentions: dict[str, int]) -> str:
    """Build the message. Names resolve to a real ping when mapped."""

    def who(player: Player) -> str:
        user_id = mentions.get(player.name.strip())
        return f"<@{user_id}>" if user_id else f"**{player.name.strip()}**"

    done = [p for p in report.players if p.done]
    lines = [
        f"**Pairing** - {report.matchups_total} duels par joueur. "
        f"{len(done)}/{len(report.players)} complets."
    ]

    if report.matchups_missing:
        lines.append("")
        lines.append("**Matchups incomplets**")
        for player in report.matchups_missing:
            lines.append(f"- {who(player)} : {max(player.avec, player.sans)}/{report.matchups_total}")

    if report.liste_missing:
        lines.append("")
        lines.append("**Listes a fournir**")
        for player in report.liste_missing:
            lines.append(f"- {who(player)} : {player.liste_label}")

    if not report.matchups_missing and not report.liste_missing:
        lines.append("")
        lines.append("Tout est bon.")

    return "\n".join(lines)
