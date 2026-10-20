"""The term corpus is rebuilt from the rules export, not stored.

The output is a build artifact - 11,303 terms on the 2026-10-19 export, 95% of a
one-minute chunk - so what is kept is the process. It is tested against a
miniature export in the export's own layout, because the real one lives outside
this repository and moves.
"""

import json

import pytest

from tools.build_glossary_corpus import build, write


def _export(root):
    """The smallest export the module accepts, pipe-delimited as the real one is."""
    root.mkdir(parents=True, exist_ok=True)

    def table(name, rows):
        (root / name).write_text("\n".join(rows) + "\n", encoding="utf-8")

    table("Factions.csv", [
        "id|name|link|",
        "AC|Adeptus Custodes|",
        "AE|Aeldari|",
        "DRU|Drukhari|",
        "NEC|Necrons|",
        "SM|Space Marines|",
    ])
    table("Datasheets.csv", [
        "id|name|faction_id|",
        "1|Agamatus Custodians|AC|",
        "2|Caladius Grav-tank|AC|",
        "3|Warrior Squad|AE|",
        "4|Warrior Squad|DRU|",
        "5|Warrior Squad|NEC|",
        "6|Warrior Squad|SM|",
        "7|Kabalite Warriors|DRU|",
        "8|A datasheet name that is really a whole sentence of prose, cut here|AC|",
        "9|Colon: a name that leaked out of a prose column|AC|",
    ])
    table("Datasheets_wargear.csv", ["datasheet_id|name|", "7|Splinter Rifle|"])
    table("Datasheets_keywords.csv", [
        "datasheet_id|keyword|is_faction_keyword|",
        "7|Kabal|true|",
        "7|Infantry|false|",
    ])
    table("Stratagems.csv", ["faction_id|name|", "DRU|Cruel Amusement|"])
    table("Enhancements.csv", ["faction_id|name|", "DRU|Ancient Evil|"])
    table("Detachments.csv", ["faction_id|name|", "DRU|Realspace Raiders|"])
    table("Detachment_abilities.csv", ["faction_id|name|", "DRU|Webway Strike|"])
    table("Abilities.csv", ["faction_id|name|", "DRU|Power from Pain|"])
    return root


@pytest.fixture
def corpus(tmp_path):
    return build(_export(tmp_path / "export"))


# -- what the two tiers are for ----------------------------------------------


def test_the_table_tier_holds_what_is_said_out_loud(corpus):
    assert "Agamatus Custodians" in corpus.table["AC"]
    assert "Splinter Rifle" in corpus.table["DRU"]


def test_a_stratagem_is_paper_and_not_table(corpus):
    """A stratagem is read off the card; it is not something you say."""
    assert "Cruel Amusement" in corpus.paper["DRU"]
    assert "Cruel Amusement" not in corpus.table["DRU"]


def test_a_keyword_counts_only_when_it_is_the_factions_own(corpus):
    assert "Kabal" in corpus.table["DRU"]
    assert "Infantry" not in corpus.table["DRU"]


# -- what a naive list gets wrong ---------------------------------------------


def test_a_word_inside_a_name_is_kept(corpus):
    """A player says "les Agamatus", never "Agamatus Custodians"."""
    assert "Caladius" in corpus.table["AC"]


def test_an_ordinary_word_inside_a_name_is_not(corpus):
    """"Warrior Squad" is a name; "Warrior" belongs to four factions and is not."""
    assert "Warrior Squad" in corpus.table["AE"]
    assert "Warrior" not in corpus.table["AE"]


def test_the_aliases_the_corpus_cannot_derive_are_added(corpus):
    """11th edition renamed the range; nobody at the table noticed."""
    assert "Eldar" in corpus.table["AE"]
    assert "Dark Eldar" in corpus.table["DRU"]


def test_a_paragraph_in_a_name_column_is_not_a_name(corpus):
    long_one = "A datasheet name that is really a whole sentence of prose, cut here"
    assert long_one not in corpus.table["AC"]
    assert not any(":" in term for term in corpus.table["AC"])


def test_an_empty_name_is_never_a_term(corpus):
    assert all(term for terms in corpus.table.values() for term in terms)


# -- the output ---------------------------------------------------------------


def test_write_puts_each_tier_in_its_own_file(tmp_path):
    out = tmp_path / "out"
    index = write(build(_export(tmp_path / "export2")), out)

    assert (out / "DRU.txt").is_file()
    assert (out / "DRU.paper.txt").is_file()
    assert (out / "_core.txt").is_file()
    assert "Cruel Amusement" not in (out / "DRU.txt").read_text(encoding="utf-8")
    assert "Cruel Amusement" in (out / "DRU.paper.txt").read_text(encoding="utf-8")
    assert index["DRU"]["table"] > 0 and index["DRU"]["paper"] > 0


def test_the_index_says_what_each_faction_costs(tmp_path):
    out = tmp_path / "out"
    index = write(build(_export(tmp_path / "export3")), out)
    on_disk = json.loads((out / "index.json").read_text(encoding="utf-8"))

    assert on_disk == index
    assert on_disk["AC"]["faction"] == "Adeptus Custodes"
    assert on_disk["_core"]["chars"] > 0


def test_the_same_export_rebuilds_byte_for_byte(tmp_path):
    """It is diffed and rebuilt on other machines, so it must not wobble."""
    first, second = tmp_path / "one", tmp_path / "two"
    write(build(_export(tmp_path / "export4")), first)
    write(build(_export(tmp_path / "export5")), second)

    for path in sorted(first.iterdir()):
        assert path.read_bytes() == (second / path.name).read_bytes(), path.name
        assert b"\r\n" not in path.read_bytes(), "LF, not the platform's newline"


def test_a_wrong_path_is_an_error_not_an_empty_corpus(tmp_path):
    with pytest.raises(FileNotFoundError):
        build(tmp_path / "not-an-export")
