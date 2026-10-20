"""Rebuild the Warhammer 40,000 term corpus from a rules-repository CSV export.

`glossary.txt` is 114 hand-picked terms and stays that way: it is attached to
every transcription request, so it holds only what is the same for everybody.
This builds the larger corpus behind it - every name the books carry, split into
what a player says at the table and what is only ever read on paper - from the
same CSV export the rules repository builds its own index from.

The output is a build artifact, not a source file: run it again when the export
moves. Nothing it writes is committed; this process is.

    python tools/build_glossary_corpus.py --corpus <csv dir> --out <dir>

The input contract is the export's own layout, pipe-delimited:

    Factions.csv                id, name
    Datasheets.csv              id, name, faction_id
    Datasheets_wargear.csv      datasheet_id, name
    Datasheets_keywords.csv     datasheet_id, keyword, is_faction_keyword
    Stratagems.csv              faction_id, name
    Enhancements.csv            faction_id, name
    Detachments.csv             faction_id, name
    Detachment_abilities.csv    faction_id, name
    Abilities.csv               faction_id, name

Measured on the 2026-10-19 export: 11,303 terms over 24 factions - 8,194 of
them named at the table, 3,059 read off the paper, and 50 game-wide - about
41,900 tokens. That is 95% of a one-minute chunk, which is why the bot ships the
114-term head instead - see docs/glossary-corpus.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# A column that holds a whole rule text is not a name. Datasheet names are
# short; anything with a sentence, a colon or this many characters in it leaked
# in from a prose column.
MAX_NAME_CHARS = 60

# The words inside a name matter as much as the name: a player says "les
# Agamatus", never "Agamatus Custodians", so a list of whole names contains no
# word of half the things said. A word that belongs to more than this many
# factions is ordinary - "Warriors", "Squad", "Lord" - and would only dilute the
# list and invite the model to write it where nobody said it.
DISTINCTIVE_FACTIONS = 3

MIN_TERM_CHARS = 3
MIN_WORD_CHARS = 4

STOP_WORDS = frozenset(
    """
    the of and a to in is it that for on with as this are be by or from at an
    la le les des du de un une et en sur dans pour par avec sans
    """.split()
)

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9'\u00c0-\u00ff-]+")
_FACTION_ID = re.compile(r"[A-Za-z0-9_]{1,8}")

# The names players actually say are the old ones. 11th edition renamed half the
# range and "Eldar" is not a substring of "Aeldari", so no amount of reading the
# corpus produces them. These are the aliases the corpus cannot derive.
ALIASES: dict[str, list[str]] = {
    "AE": ["Eldar", "Eldars", "Asuryani", "Craftworld", "Avatar", "Farseer", "Warlock", "Wraithlord"],
    "DRU": ["Dark Eldar", "Eldars noirs", "Kabalite", "Kabalites", "Wyches", "Haemonculus", "Succubus"],
    "NEC": ["Necron", "Necrons", "C'tan", "Monolith", "Doomsday Ark"],
    "ORK": ["Ork", "Orks", "Boyz", "Waaagh", "Warboss"],
    "TAU": ["Tau", "T'au", "Crisis", "Broadside", "Riptide"],
    "SM": ["Space Marine", "Space Marines", "Marines", "Astartes", "Land Raider", "Dreadnought"],
    "CSM": ["Chaos Marine", "Chaos Space Marine", "Traitor Marines", "Havocs", "Obliterators"],
    "AM": ["Guard", "Imperial Guard", "Astra Militarum", "Leman Russ", "Baneblade"],
    "TYR": ["Tyranid", "Tyranids", "Nids", "Gaunt", "Hormagaunt", "Termagant", "Carnifex", "Hive Tyrant"],
    "CD": ["Daemon", "Daemons", "Bloodletter", "Plaguebearer", "Great Unclean One"],
    "AC": ["Custodes", "Custodian", "Custodians", "Agamatus", "Vertus Praetor", "Allarus"],
    "GK": ["Grey Knight", "Grey Knights", "Nemesis", "Dreadknight"],
    "AS": ["Sororitas", "Sisters", "Battle Sister", "Exorcist", "Penitent Engine"],
    "AdM": ["Mechanicus", "Skitarii", "Kataphron", "Tech-priest", "Onager"],
    "DG": ["Death Guard", "Plague Marine", "Plagueburst", "Mortarion"],
    "TS": ["Thousand Sons", "Rubric", "Tzaangor", "Ahriman", "Magnus"],
    "WE": ["World Eaters", "Berzerker", "Khorne", "Angron"],
    "EC": ["Emperor's Children", "Noise Marine", "Fulgrim"],
    "GC": ["Genestealer", "Genestealers", "Aberrant", "Acolyte", "Patriarch"],
    "QI": ["Imperial Knight", "Knights", "Knight Castellan"],
    "QT": ["Chaos Knight", "War Dog"],
    "LoV": ["Votann", "Leagues", "Hearthkyn", "Sagitaur", "Hekaton"],
    "AoI": ["Agents", "Assassin", "Inquisitor", "Vindicare", "Culexus"],
    "TL": ["Titan", "Titans", "Warlord Titan", "Warhound"],
}

# Game-wide vocabulary, useful whatever is on the table. It is deliberately not
# the bot's shipped list: this one is the corpus side of the same idea.
CORE: tuple[str, ...] = tuple(sorted({
    "Warhammer", "Wahammer", "Warhammer 40,000", "40K", "Codex", "Detachment",
    "Stratagem", "Stratagems", "Relic", "Warlord", "Warlord trait", "Enhancement",
    "Command point", "Battle-shock", "Deep Strike", "Feel No Pain", "Lone Operative",
    "Stealth", "Devastating Wounds", "Lethal Hits", "Sustained Hits", "Twin-linked",
    "Invulnerable save", "Objective", "Primary", "Secondary", "Deployment",
    "Reinforcements", "Reserves", "Charge", "Advance", "Fall Back", "Overwatch",
    "Fight phase", "Shooting phase", "Movement phase", "Command phase", "Charge phase",
    "Bolter", "Bolt pistol", "Plasma", "Melta", "Flamer", "Lascannon", "Missile launcher",
    "Power fist", "Chainsword", "Thunder hammer", "Storm shield", "Grenade",
}))

# Without these two there is no corpus at all, and an empty one would look like
# an empty export rather than a wrong path.
REQUIRED = ("Factions.csv", "Datasheets.csv")

_PAPER_SOURCES = (
    "Stratagems.csv",
    "Enhancements.csv",
    "Detachments.csv",
    "Detachment_abilities.csv",
    "Abilities.csv",
)


@dataclass(frozen=True)
class Corpus:
    """The terms, by faction, in the two tiers the table and the paper need."""

    factions: dict[str, str]
    table: dict[str, tuple[str, ...]]
    paper: dict[str, tuple[str, ...]]
    core: tuple[str, ...]

    @property
    def terms(self) -> int:
        return (
            sum(len(values) for values in self.table.values())
            + sum(len(values) for values in self.paper.values())
            + len(self.core)
        )


def _rows(path: Path) -> list[dict[str, str]]:
    """Read one export table. Missing is empty; the encoding is not always UTF-8."""
    if not path.is_file():
        return []
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle, delimiter="|"))
        except UnicodeDecodeError:
            continue
    return []


def _clean(value: object) -> str:
    """A name, not a paragraph. Some columns hold whole rule texts."""
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = text.replace("\u2019", "'").replace("\u2013", "-").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > MAX_NAME_CHARS or ". " in text or ":" in text:
        return ""
    return text


def _words_in(name: str) -> list[str]:
    """The words of a name that could be said on their own."""
    return [
        word
        for word in _WORD_SPLIT.split(name)
        if len(word) >= MIN_WORD_CHARS and word.lower() not in STOP_WORDS
    ]


def _distinctive(by_faction: dict[str, set[str]]) -> dict[str, set[str]]:
    """The words inside names that belong to only a few factions."""
    owners: dict[str, set[str]] = defaultdict(set)
    for fid, names in by_faction.items():
        for name in names:
            for word in _words_in(name):
                owners[word].add(fid)

    out: dict[str, set[str]] = defaultdict(set)
    for fid, names in by_faction.items():
        for name in names:
            for word in _words_in(name):
                if len(owners[word]) <= DISTINCTIVE_FACTIONS:
                    out[fid].add(word)
    return out


def _terms(values: set[str]) -> tuple[str, ...]:
    return tuple(sorted({v for v in values if v and len(v) >= MIN_TERM_CHARS}))


def _keeps(fid: str) -> bool:
    return bool(_FACTION_ID.fullmatch(str(fid)))


def build(corpus_dir: Path) -> Corpus:
    """Read the export and produce the two tiers."""
    for name in REQUIRED:
        if not (corpus_dir / name).is_file():
            raise FileNotFoundError(
                "%s is not a rules-corpus export: %s is missing"
                % (corpus_dir, name)
            )

    factions = {
        row["id"]: _clean(row.get("name"))
        for row in _rows(corpus_dir / "Factions.csv")
        if row.get("id")
    }
    sheets = {
        row["id"]: row for row in _rows(corpus_dir / "Datasheets.csv") if row.get("id")
    }

    table: dict[str, set[str]] = defaultdict(set)
    paper: dict[str, set[str]] = defaultdict(set)

    for sheet in sheets.values():
        table[sheet["faction_id"]].add(_clean(sheet.get("name")))
    for row in _rows(corpus_dir / "Datasheets_wargear.csv"):
        sheet = sheets.get(row.get("datasheet_id"))
        if sheet:
            table[sheet["faction_id"]].add(_clean(row.get("name")))
    for row in _rows(corpus_dir / "Datasheets_keywords.csv"):
        sheet = sheets.get(row.get("datasheet_id"))
        if sheet and row.get("is_faction_keyword") == "true":
            table[sheet["faction_id"]].add(_clean(row.get("keyword")))
    for name in _PAPER_SOURCES:
        for row in _rows(corpus_dir / name):
            if row.get("faction_id"):
                paper[row["faction_id"]].add(_clean(row.get("name")))

    for fid, extra in ALIASES.items():
        table.setdefault(fid, set()).update(extra)
    for fid, words in _distinctive(table).items():
        table[fid] |= words

    return Corpus(
        factions=factions,
        table={fid: _terms(v) for fid, v in table.items() if _keeps(fid)},
        paper={fid: _terms(v) for fid, v in paper.items() if _keeps(fid)},
        core=CORE,
    )


def _write_text(path: Path, text: str) -> None:
    """LF, UTF-8, no BOM: the file is diffed and rebuilt on other machines.

    Path.write_text translates to the platform's newline on Windows, which made
    the index differ from the term files it describes.
    """
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _write_lines(path: Path, lines: tuple[str, ...]) -> None:
    _write_text(path, "\n".join(lines) + "\n")


def write(corpus: Corpus, out_dir: Path) -> dict[str, dict[str, object]]:
    """Write one file per faction per tier, the core list, and the index."""
    out_dir.mkdir(parents=True, exist_ok=True)
    index: dict[str, dict[str, object]] = {}
    for fid in sorted(set(corpus.table) | set(corpus.paper)):
        table = corpus.table.get(fid, ())
        paper = corpus.paper.get(fid, ())
        if table:
            _write_lines(out_dir / ("%s.txt" % fid), table)
        if paper:
            _write_lines(out_dir / ("%s.paper.txt" % fid), paper)
        index[fid] = {
            "faction": corpus.factions.get(fid, fid),
            "table": len(table),
            "paper": len(paper),
            "chars": sum(len(t) for t in table) + sum(len(p) for p in paper),
        }

    _write_lines(out_dir / "_core.txt", corpus.core)
    index["_core"] = {
        "faction": "game-wide vocabulary",
        "table": len(corpus.core),
        "paper": 0,
        "chars": sum(len(term) for term in corpus.core),
    }
    _write_text(
        out_dir / "index.json",
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the 40k term corpus from a rules-repository CSV export."
    )
    parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        help="directory holding the rules repository's CSV export",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("workspace/glossary"),
        help="where to write the term files (default: workspace/glossary)",
    )
    args = parser.parse_args(argv)

    corpus = build(args.corpus)
    index = write(corpus, args.out)
    chars = sum(int(entry["chars"]) for entry in index.values())
    print(
        "%d terms over %d factions, %d characters, ~%d tokens"
        % (corpus.terms, len(index) - 1, chars, chars // 4)
    )
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
