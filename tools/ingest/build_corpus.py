"""Build the Tier 3 knowledge corpus — from the game's own prose, not from a wiki.

Input : data/raw/tables/en_DT_*Text*.json   (the English text tables)
        data/raw/items.json                 (item id -> name, for titles and markup)
        data/<version>/lexicon.json         (canonical entities, for the entity tags)
        data/<version>/tech.json            (technology names, for titles)
Output: data/<version>/corpus.json

## Why this exists at all, and what it changes

Assumption **A7** survived Phase 0 as the project's last open licensing risk, narrowed by
[ADR-0014](../../Docs/adr/0014-game-files-as-source.md) to exactly one thing: the Q7 prose
corpus, listed there as "licensed community prose" - the only dataset still expected to
come from outside the pak.

**It does not have to.** Palworld ships 45 developer-written help entries explaining its
own mechanics (Pal Breeding Farm, Elements, Sanity, Item Rot, Pal Rank & Essence
Condensers), 310 Paldeck descriptions, 64 journal notes and several hundred item, build
and technology descriptions. That is a prose corpus about Palworld written by the people
who made Palworld, extracted from a copy of the game the user owns - which is the exact
posture ADR-0014 used to remove the licence risk from the other six datasets.

So A7's remaining risk is not mitigated here, it is **absent**. Nothing in this file comes
from a community source.

## What that costs, stated rather than glossed

The game explains its own mechanics and says nothing about how to play well. There is no
optimal breeding route in here, no tier list, no "actually the trick is". A corpus of the
game's own text can answer *"how does the breeding farm work"* and cannot answer *"what is
the best base layout"*, and the second is a real question players ask. That is a genuine
narrowing against the roadmap's Q7, and the honest trade for a corpus with no licence
question attached and a citation on every line.

## The source list was drawn from a survey, not from what happened to be extracted

Worth recording, because the first version of this file was built from the 81 tables
already sitting in `data/raw/tables/` — and the pak has 532. "I searched for it" is only
as strong as the term searched for, which this project has written down twice.

`dotnet run -- tables` lists every one. Filtered to anything named `*Text*` or `*Desc*`,
there are 55, and all but three are either already used below, a **name table** this file
reads as a resolver rather than as prose (item, Pal, skill, map-object, technology, UI),
or a rich-text style definition.

The three that were neither were exported and measured rather than assumed:

| table | rows | verdict |
|---|---|---|
| `DT_PalShortDescriptionText` | 113 | **Japanese only** — no `/L10N/en/` copy exists in the pak |
| `DT_BaseCampWorkerEventText` | 9 | **Japanese only**, same |
| `DT_SystemLocalize` | 4 | **Japanese only**, and "Yes"/"No"/"OK" regardless |

So nothing usable was missing. They are not left in `data/raw/tables/` afterwards: a
Japanese `DT_*Text` sitting beside an English `en_DT_*Text` is exactly the collision that
once published every item name in Japanese, and three files' worth of that trap is not
worth keeping as evidence when one command re-derives it.

## Two exclusions worth stating

* **NPC dialogue is left out** - 832 entries and 179k characters of it. It is in-character
  speech: a character's opinion, delivered in their voice, sometimes wrong on purpose. A
  retrieval corpus cannot tell that apart from a mechanics explanation, and a card citing
  a merchant's banter as how the game works would be the confidently-wrong answer this
  project refuses, dressed in a source attribution.
* **Tutorial prompts are left out.** They are control bindings wrapped in key-icon markup
  ("Pick up items with <keyGuideIcon .../>"), which is a UI instruction rather than
  knowledge, and which renders as nothing useful once the markup is stripped.

## Markup, again

The text tables are full of `<itemName id=|X|/>`, `<mapObjectName .../>`, `<uiCommon .../>`
and key-guide icons. Resolvable tags are replaced with the English name; the rest are
**stripped, never published**. build_tech.py shipped raw markup as 26 technology names by
matching a tag case-sensitively, and that is one occurrence too many for this file to
publish a `<` to a card.

Usage: python tools/ingest/build_corpus.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"
TABLES = RAW / "tables"

# Any inline tag. The two that resolve to a name are handled; everything else - key
# icons, colour spans, ruby text - is removed, because a card must never show markup.
#
# The id is NOT always the last attribute: `<itemName id=|Wool| style=|Status_Keyword|/>`
# is common, and a pattern insisting on `/>` immediately after it fell through to the
# catch-all and deleted the whole tag. That silently dropped the item's name out of the
# sentence - "Sometimes drops when assigned to Ranch" - which is the hole-in-the-text
# failure the UNRESOLVED sentinel exists to catch, arriving by a route the sentinel could
# not see.
ANY_TAG = re.compile(r"<([a-zA-Z]+)\s+id=\|([^|]+)\|[^>]*?/?>|<[^>]{0,120}>")
PLACEHOLDER = {"en text", ""}

# The same untranslated marker, but INSIDE a sentence rather than as the whole string:
# Menasting's Paldeck line reads "Some say a beam from a en_text that has...". A
# whole-string check misses it, and the result is a chunk that is quoted verbatim onto a
# card with a placeholder sitting in the middle of it.
INLINE_PLACEHOLDER = re.compile(r"\ben[_ ]text\b", re.I)

# Chunks shorter than this are not prose. Most of them are an item description that
# repeats the item's own name ("Animal Skin"), which retrieves against everything and
# tells the reader nothing they did not already have on the card that led them here.
MIN_CHARS = 40


def _rows(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc = doc[0] if isinstance(doc, list) else doc
    return doc.get("Rows", doc)


def _text(entry) -> str | None:
    if not isinstance(entry, dict):
        return None
    data = entry.get("TextData")
    if not isinstance(data, dict):
        return None
    s = (data.get("LocalizedString") or data.get("SourceString") or "").strip()
    return None if s.lower() in PLACEHOLDER else s


# Tags that carry no name and are pure decoration: inline icons, key-guide glyphs, the
# number-colouring spans. Removing them leaves the sentence intact.
DECORATIVE = {"img", "keyguideicon", "numblue", "numred"}

# Emitted where a NAMED tag could not be resolved, so the chunk can be dropped instead of
# published with a hole in it. A Paldeck line reading "increases 's Defense" is not
# wrong, but it is broken text presented as the game's own words, and a citation makes
# that worse rather than better.
UNRESOLVED = "�"


class Resolver:
    """Turns inline markup into English, decoration into nothing, and the rest into a
    sentinel that gets the whole chunk dropped."""

    def __init__(self, items: dict, map_objects: dict, ui: dict, characters: dict,
                 skills: dict):
        self._items = {k.lower(): v for k, v in items.items()}
        self._objects = {k.lower(): v for k, v in map_objects.items()}
        self._ui = {k.lower(): v for k, v in ui.items()}
        self._characters = {k.lower(): v for k, v in characters.items()}
        self._skills = {k.lower(): v for k, v in skills.items()}

    def _name(self, kind: str, ref: str) -> str:
        kind = kind.lower()
        if kind in DECORATIVE:
            return ""
        if kind == "itemname":
            got = (self._items.get(ref.lower()) or {}).get("name")
        elif kind == "mapobjectname":
            got = _text(self._objects.get(f"mapobject_name_{ref}".lower()))
        elif kind == "uicommon":
            got = _text(self._ui.get(ref.lower()))
        elif kind == "charactername":
            got = _text(self._characters.get(f"pal_name_{ref}".lower())) \
                or _text(self._characters.get(ref.lower()))
        elif kind == "activeskillname":
            got = _text(self._skills.get(f"action_skill_{ref}".lower())) \
                or _text(self._skills.get(ref.lower()))
        else:
            got = None
        return got or UNRESOLVED

    def clean(self, raw: str) -> str:
        def replace(m: re.Match) -> str:
            if m.group(1) is None:
                return ""            # a bare tag with no id - a colour span, a break
            return self._name(m.group(1), m.group(2))

        out = ANY_TAG.sub(replace, raw)
        out = out.replace("\r\n", "\n").replace("\r", "\n")
        # Collapse the whitespace the removals leave behind, but keep paragraph breaks:
        # help entries are sectioned and the blank lines are what makes them chunkable.
        out = re.sub(r"[ \t]+", " ", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return "\n".join(line.strip() for line in out.split("\n")).strip()


def _pal_names(version: str) -> dict[str, str]:
    """internal id (lower) -> display name, from the lexicon this project already builds."""
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    return {i.lower(): p["canonical"]
            for p in lexicon["pals"] for i in p["internal_ids"]}


def _tech_titles(version: str) -> dict[str, str]:
    """Description key -> the technology's English name.

    Goes through tech.json rather than re-resolving the name markup, so a technology is
    called the same thing in a corpus citation and on a Q6 card.
    """
    path = REPO / "data" / version / "tech.json"
    if not path.exists():
        return {}
    # Only technologies whose NAME resolved. tech.json falls back to the tech id for 11
    # of them, which is legible enough on a Q6 card beside a level and a cost and is not
    # legible at all as a citation - "Technology: Shield_05" is the card asking to be
    # trusted while showing its internals.
    names = {e["tech_id"]: e["name"]
             for e in json.loads(path.read_text(encoding="utf-8"))["entries"]
             if e["name_source"] != "tech_id"}
    rows = _rows(RAW / "tech_recipe_unlock.json")
    return {row["Description"]: names[tech_id]
            for tech_id, row in rows.items()
            if tech_id in names and row.get("Description")}


def build(version: str) -> dict:
    items = json.loads((RAW / "items.json").read_text(encoding="utf-8"))
    map_objects = _rows(TABLES / "en_DT_MapObjectNameText_Common.json")
    ui = _rows(TABLES / "en_DT_UI_Common_Text_Common.json")
    skill_names = _rows(TABLES / "en_DT_SkillNameText_Common.json")
    characters = _rows(TABLES / "en_DT_PalNameText_Common.json")
    characters |= _rows(TABLES / "en_DT_HumanNameText_Common.json")
    resolver = Resolver(items, map_objects, ui, characters, skill_names)
    pal_of = _pal_names(version)
    tech_of = _tech_titles(version)
    items_ci = {k.lower(): v for k, v in items.items()}

    # All three return "" rather than the internal ref when nothing resolves, and the
    # chunk is then dropped. Falling back to the id publishes a citation reading
    # "Item: GrapplingGun_1" - the same underscore-suffix mismatch build_tech.py records,
    # arriving in the one place the card is explicitly asking to be trusted.
    def item_title(key: str, prefix: str) -> str:
        return (items_ci.get(key[len(prefix):].lower()) or {}).get("name") or ""

    def pal_title(key: str, prefix: str) -> str:
        return pal_of.get(key[len(prefix):].lower()) or ""

    def object_title(key: str, prefix: str) -> str:
        return _text(map_objects.get(f"MAPOBJECT_NAME_{key[len(prefix):]}")) or ""

    def skill_title(key: str, _prefix: str) -> str:
        # The name table uses the SAME key as the description table. Inventing a
        # `SKILL_NAME_` prefix titled 398 chunks with their internal id, which is the
        # shape of failure this file is otherwise built to avoid - a plausible-looking
        # transformation that resolves to nothing and does not say so.
        return _text(skill_names.get(key)) or ""

    def first_line(key: str, _prefix: str) -> str:
        return ""       # filled from the text itself, below

    # (table, key prefix, section label, how to title an entry).
    #
    # **This is the source list**, and it is the whole of it - eight tables, every one
    # from the pak, drawn from the full 532-table survey recorded in the docstring rather
    # than from whatever was already extracted.
    #
    # The section label is what a citation says out loud, so it is the player's word for
    # where this came from and not the table's: "Help guide", not
    # "en_DT_HelpGuideDescText".
    #
    # Kept counts against raw, measured 2026-08-12 (the gap is short entries, unresolved
    # markup and untitled rows, all dropped rather than published):
    #
    #   Help guide      43 / 47      median 323 chars   <- the mechanics half
    #   Journal note    48 / 64      median 629         <- the longest prose in the game
    #   Paldeck        291 / 310     median 184         long description
    #   Paldeck        299 / 305     median 216         first-activation / partner skill
    #   Technology     265 / 381     median 115
    #   Structure      403 / 501     median 103
    #   Item         1,383 / 1,831   median 134         <- the bulk, and the thinnest
    #   Skill          371 / 432     median 104
    SOURCES = [
        ("en_DT_HelpGuideDescText", "", "Help guide", first_line),
        ("en_DT_NoteDescText", "", "Journal note", first_line),
        ("en_DT_PalLongDescriptionText", "PAL_LONG_DESC_", "Paldeck", pal_title),
        ("en_DT_PalFirstActivatedInfoText", "PAL_FIRST_SPAWN_DESC_", "Paldeck",
         pal_title),
        # Empty rather than the key when nothing owns the description: 119 rows here
        # belong to no technology, and a citation reading "DESC_RECIPE_AncientSpa" is
        # worse than not citing the row at all. An untitled chunk is dropped below.
        ("en_DT_TechnologyDescText_Common", "", "Technology",
         lambda key, _p: tech_of.get(key, "")),
        ("en_DT_BuildObjectDescText_Common", "BUILDOBJECT_DESC_", "Structure",
         object_title),
        ("en_DT_ItemDescriptionText_Common", "ITEM_DESC_", "Item", item_title),
        ("en_DT_SkillDescText_Common", "", "Skill", skill_title),
    ]

    chunks, skipped_short, skipped_markup = [], 0, 0
    for table, prefix, section, titler in SOURCES:
        path = TABLES / f"{table}.json"
        if not path.exists():
            print(f"  (no {table} - skipping)", file=sys.stderr)
            continue
        for key, entry in _rows(path).items():
            raw = _text(entry)
            if raw is None:
                continue
            body = resolver.clean(raw)
            if ("<" in body or "|" in body or UNRESOLVED in body
                    or INLINE_PLACEHOLDER.search(body)):
                # Unhandled markup, or a named tag that resolved to nothing. Dropped
                # rather than published: this project has already shipped markup as a
                # name once, and a sentence with a hole where a Pal's name should be is
                # the same failure wearing better clothes.
                skipped_markup += 1
                continue
            title = titler(key, prefix)
            if not title:
                title, _, body = body.partition("\n")
                title, body = title.strip(), body.strip()
            if not title or len(body) < MIN_CHARS or body == title:
                # No title is a drop, not a fallback to the internal key. A citation
                # reading "ACTION_SKILL_AirBlade" tells the reader nothing and looks like
                # a bug in the one place the card is asking to be trusted.
                skipped_short += 1
                continue
            chunks.append({
                "chunk_id": f"{section.lower().replace(' ', '_')}:{key}",
                "title": title,
                "section": section,
                "text": body,
                "source_key": key,
                "source_table": table,
            })

    # Entity tags, from the same canonical names the lexicon gives the router. This is
    # the "entity boost" half of ADR-0011's hybrid retrieval, and it has to use the same
    # vocabulary or a boost will fire on a name the query could never produce.
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    canonical = [p["canonical"] for p in lexicon["pals"]]
    canonical += [r["canonical"].replace("_", " ") for r in lexicon["resources"]]
    patterns = [(c, re.compile(rf"\b{re.escape(c)}\b", re.I)) for c in canonical
                if len(c) > 3]
    for chunk in chunks:
        haystack = f"{chunk['title']}\n{chunk['text']}"
        chunk["entities"] = sorted(c for c, p in patterns if p.search(haystack))

    by_section: dict[str, int] = {}
    for c in chunks:
        by_section[c["section"]] = by_section.get(c["section"], 0) + 1

    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "the game's own English text tables - help guide, Paldeck, journal "
                  "notes, technology, structure, item and skill descriptions",
        "licence_note": "A7's remaining licensing risk was the Q7 prose corpus, which "
                        "ADR-0014 expected to come from licensed community writing. It "
                        "does not: this corpus is the game's own text, extracted from a "
                        "copy the user owns, which is the same posture that removed the "
                        "risk from every other dataset here. Nothing in this file comes "
                        "from a community source.",
        "coverage_note": "The game explains its mechanics and says nothing about playing "
                         "well. This can answer 'how does the breeding farm work' and "
                         "cannot answer 'what is the best base layout'. That is a real "
                         "narrowing against the roadmap's Q7 and the honest cost of a "
                         "corpus with no licence question attached.",
        "exclusion_note": "NPC dialogue (832 entries) is EXCLUDED: it is in-character "
                          "speech and a retrieval index cannot tell an opinion from a "
                          "mechanic. Tutorial prompts are excluded as control bindings.",
        "markup_note": "Inline tags are resolved to English where they name an item, a "
                       "structure or a UI string, and stripped otherwise. Any chunk still "
                       "containing markup after that is DROPPED, not published.",
        "stats": {
            "chunks": len(chunks),
            "characters": sum(len(c["text"]) for c in chunks),
            "by_section": dict(sorted(by_section.items())),
            "with_entities": sum(1 for c in chunks if c["entities"]),
            "skipped_too_short": skipped_short,
            "skipped_unresolved_markup": skipped_markup,
        },
        "chunks": sorted(chunks, key=lambda c: c["chunk_id"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    if not (TABLES / "en_DT_HelpGuideDescText.json").exists():
        sys.exit(f"Missing {TABLES / 'en_DT_HelpGuideDescText.json'}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- tables")

    data = build(args.version)
    s = data["stats"]
    dest = REPO / "data" / args.version / "corpus.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"corpus -> {dest}")
    print(f"  chunks       {s['chunks']}  ({s['characters'] // 1000}k characters)")
    print(f"  by section   " + ", ".join(f"{k} {v}" for k, v in s["by_section"].items()))
    print(f"  entity-tagged {s['with_entities']}")
    print(f"  skipped      {s['skipped_too_short']} too short, "
          f"{s['skipped_unresolved_markup']} with markup we could not resolve")


if __name__ == "__main__":
    main()
