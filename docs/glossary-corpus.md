# The 40k term corpus

*Why the bot ships 128 names, how the other 11,000 are rebuilt, and what the rules app does with them.*

## Two lists, two jobs

`glossary.txt` is attached to every transcription request, so it is paid on every
chunk: 128 terms, a few hundred tokens a call (452 measured at 114). That is the head, and it holds only what is
the same for everybody — the factions under their printed and their spoken names,
the rule terms, the wargear people name out loud, and the loanwords a tournament
week runs on. Its size is the decision; see the README for how to tune it.

Behind it sits the corpus: every name the books carry. **11,303 terms, about
41,900 tokens** on the 2026-10-19 export. That is 95% of a one-minute chunk, so
it is not the bot's list. It is where the head is picked from, and it is what the
rules app asked for: its own glossary leaves 6,029 of 10,222 corpus vocabulary
entries uncovered, and the remainder is the weapon and stratagem vocabulary the
faction pack never publishes.

## The chain

    rules export (CSV)  ->  tools/build_glossary_corpus.py  ->  <fid>.txt
                                                                <fid>.paper.txt
                                                                _core.txt
                                                                index.json

Only the middle link belongs to this repository. The export is the rules
repository's own `workspace/rules-corpus/csv`, untracked there by design and
rebuilt upstream; the term files are a build artifact. Neither is committed, and
neither needs to be stored anywhere: the whole thing rebuilds in about a second.

## Running it

```bash
python tools/build_glossary_corpus.py --corpus /path/to/rules-corpus/csv --out workspace/glossary
```

It prints the term count, the character count and a token estimate, and writes
LF, UTF-8, no BOM — the output is diffed, so it must not wobble between machines.
Running it twice on the same export produces byte-identical files.

## What it reads

Pipe-delimited, from the export directory. `Factions.csv` and `Datasheets.csv`
are required; a missing one is an error rather than an empty corpus.

| File | Columns used | Tier |
|---|---|---|
| `Factions.csv` | `id`, `name` | — |
| `Datasheets.csv` | `id`, `name`, `faction_id` | table |
| `Datasheets_wargear.csv` | `datasheet_id`, `name` | table |
| `Datasheets_keywords.csv` | `datasheet_id`, `keyword`, `is_faction_keyword` | table |
| `Stratagems.csv` | `faction_id`, `name` | paper |
| `Enhancements.csv` | `faction_id`, `name` | paper |
| `Detachments.csv` | `faction_id`, `name` | paper |
| `Detachment_abilities.csv` | `faction_id`, `name` | paper |
| `Abilities.csv` | `faction_id`, `name` | paper |

## The two tiers

**Table** is what gets named out loud while a game is being played: datasheets,
wargear, faction keywords. 8,194 terms on that export.

**Paper** is what is read off a card and rarely said: stratagems, enhancements,
detachments, detachment abilities, unit abilities. 3,059 terms.

A term list that mixes them is not wrong, it is just bigger than it needs to be
for the one job and smaller than it needs to be for the other.

## Three things a naive list gets wrong

**A word inside a name is not the name.** A player says *les Agamatus*, never
*Agamatus Custodians*, so a list of whole names contains no word of half the
things said. Every name is split into its words, and a word is kept only when it
belongs to three factions or fewer — `Agamatus` is a name, `Warriors` is
ordinary and would only invite the model to write it where nobody said it.

**The corpus cannot know what players actually say.** 11th edition renamed half
the range and `Eldar` is not a substring of `Aeldari`, so no amount of reading
the export produces the old names. Those are hand-written aliases in the module,
one list per faction.

**Some columns hold prose.** A name column that carries a sentence, a colon or
more than 60 characters is dropped rather than added as a term.

## Where it goes next

The head stays hand-tuned: it is the list a session actually gets wrong, and the
rule is to add a line when a real meeting spells a name wrong. The corpus is the
pool those lines come from, and the thing to hand over when another project needs
the vocabulary of the game rather than the vocabulary of the meeting.
