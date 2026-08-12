"""Build the technology dataset — what can be unlocked, at what level, for what price.

Input : data/raw/tech_recipe_unlock.json                     (DT_TechnologyRecipeUnlock)
        data/raw/tech_names_en.json                          (the tech's own name row)
        data/raw/items.json                                  (item id -> English name, type)
        data/raw/tables/en_DT_MapObjectNameText_Common.json  (build object -> English name)
Output: data/<version>/tech.json

**Everything here is stated by the game.** A technology has a `LevelCap` or it does not,
costs a `Cost`, is or is not an `IsBossTechnology`, and names its prerequisite outright.
Nothing is inferred from a name prefix, with one exception that is declared below and
carried in the output as `tower_join_note`.

## The tree is not a tree

The roadmap's Phase 4 line is "tech tree ingest; validate the prerequisite graph", which
assumed a graph worth validating. Measured, **17 of 588 rows have a prerequisite at all**,
and all 17 are links in six straight chains (AutoMealPouch 1-5, GrapplingGun 1-5,
AdditionalInventory 1-4, Unlock_Picking 1-3, Lantern, the electric egg incubator). Every
target exists, and there are no cycles - so the validation passes, and it passes because
there is almost nothing to validate.

**The real gate is player level.** `LevelCap` spans 1-80 and every row carries one, so
progression in this game is a level curve with a points budget, not a dependency graph.
That reshapes Q6: the interesting question is not "what have I earned the right to
research" but "what can I afford right now, and what is worth the points".

`Tier` is 0 on all 588 rows and is dropped rather than published - a column that never
varies is not a category, and shipping it would invite a card to sort by it.

## Two currencies, and they are not interchangeable

`IsBossTechnology` decides which pool pays: 51 rows spend the save's `bossTechnologyPoint`
(the game calls them Ancient Technology Points, earned from tower bosses) and the other
537 spend `TechnologyPoint`. A recommendation that adds the two together would tell a
player they can afford something they cannot, so the currency travels with the cost.

## Names come through two levels of indirection

523 of 588 tech name rows are not text - they are markup pointing at something else,
`<itemName id=|Axe_Tier_00|/>` or `<mapObjectName id=|WorkBench|/>`. Resolving that is
the ingest's real work, and it is **case-insensitive** for the reason build_mounts.py
records: the pak's casing is not trustworthy on any join (`FlameThrower` against
`ITEM_NAME_Flamethrower`).

**11 rows resolve to nothing at all** and fall back to their tech id. They are listed in
the output rather than quietly renamed: eight point at item ids with no name row
(`GrapplingGun` wants `ITEM_NAME_GrapplingGun_1`, an underscore apart) and three have no
name row of their own. Guessing the underscore rule would fix five of eleven and is
exactly the kind of derived mapping this project makes itself declare, so it is not done -
"Grappling Gun" is not worth inventing a rule for, and a tech id on a card is legible.

Usage: python tools/ingest/build_tech.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# The game's "no value" spellings, which differ per column and are all falsy in meaning
# and truthy in Python. Read as None rather than compared inline, because
# `if row["RequireTechnology"]` is True for the string "None".
NO_TECH = "None"
NO_BOSS = "EPalBossType::None"
NO_RESEARCH = "None"

BOSS_ENUM_PREFIX = "EPalBossType::"
MAP_OBJECT_NAME_PREFIX = "MAPOBJECT_NAME_"

# A tech name row is usually a pointer, not a name. Two kinds, into two different tables.
#
# **Case-insensitive on the TAG, not just on the id it points at.** Some rows spell it
# `mapObjectname`, and a case-sensitive pattern let those fall through as "unresolved",
# which published the literal string `<mapObjectname id=|MultiHatchingPalEgg|/>` as a
# technology name. Well-formed, entirely wrong, and caught only because a card was read.
# Third casing trap in this project after Boss_Anubis and SkillUnlock_Thunderdog_Ice.
NAME_TAG = re.compile(r"<(itemName|mapObjectName) id=\|([^|]+)\|/>", re.I)

# Placeholder strings the text tables carry for untranslated rows. Publishing one would be
# the failure this project keeps recording - well-formed, and wrong: "en Text" is a
# perfectly good string and a completely useless technology name.
PLACEHOLDER = {"en text", ""}

# The starting Pal Box is in the save's unlocked list and in no row of the technology
# table, because it is granted rather than researched. Recorded here so the join can
# report "1 unjoined, and it is the expected one" instead of a bare count that nobody can
# tell apart from a broken build.
GRANTED_NOT_RESEARCHED = {"PalBox"}


def _rows(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc = doc[0] if isinstance(doc, list) else doc
    return doc.get("Rows", doc)


def _text(entry: dict | None) -> str | None:
    """The English string out of a text-table row, or None when it is a placeholder."""
    if not entry:
        return None
    data = entry.get("TextData", {})
    s = (data.get("LocalizedString") or data.get("SourceString") or "").strip()
    return None if s.lower() in PLACEHOLDER else s


def _none(value: str | None, empty: str) -> str | None:
    return None if not value or value == empty else value


class Names:
    """Resolves a technology's display name through whichever table it points at.

    Case-insensitive on both lookups. That is not defensive coding: it is the third time
    this project has been bitten by the pak's casing (see build_mounts.py's casing_note
    and build_bosses.py's), and an exact match here silently drops the Flamethrower.
    """

    def __init__(self, tech_names: dict, items: dict, map_objects: dict):
        self._tech = tech_names
        self._items = {k.lower(): v for k, v in items.items()}
        self._objects = {k.lower(): v for k, v in map_objects.items()}

    def item(self, item_id: str) -> str | None:
        row = self._items.get(item_id.lower())
        return (row or {}).get("name") or None

    def map_object(self, object_id: str) -> str | None:
        return _text(self._objects.get(
            (MAP_OBJECT_NAME_PREFIX + object_id).lower()))

    def tech(self, name_key: str | None) -> tuple[str | None, str]:
        """(display name, where it came from). None when nothing resolves it."""
        raw = _text(self._tech.get(name_key or ""))
        if raw is None:
            return None, "unresolved"
        tag = NAME_TAG.fullmatch(raw)
        if tag is None:
            # 88 rows are plain English already - "Copper Smelting", "Pal Sphere".
            return raw, "literal"
        kind, ref = tag.groups()
        if kind.lower() == "itemname":
            got = self.item(ref)
            return (got, "item") if got else (None, "unresolved")
        got = self.map_object(ref)
        return (got, "map_object") if got else (None, "unresolved")


def category_of(row: dict, items: dict) -> str:
    """The game's own type for what this technology grants.

    **Deliberately the pak's `TypeA` verbatim** - Weapon, Armor, Essential, Consume - and
    `BuildObject` for the 217 rows that place a structure. A friendlier taxonomy (BASE,
    GEAR, WEAPON, INFRA, as the data model sketches) would be a mapping this project
    invented, and every card printing it would be publishing a claim rather than a fact.
    The game's word is less tidy and it is true.

    One category per technology, which was measured rather than assumed: exactly one row
    of 588 grants two types at once (a fishing rod and its bait), and it takes the first
    in sorted order so two builds agree.
    """
    if row.get("UnlockBuildObjects"):
        return "BuildObject"
    types = sorted({items[r.lower()].get("type_a") for r in row.get("UnlockItemRecipes") or []
                    if r.lower() in items and items[r.lower()].get("type_a")})
    return types[0] if types else "Unknown"


def build(version: str) -> dict:
    tech = _rows(RAW / "tech_recipe_unlock.json")
    items = json.loads((RAW / "items.json").read_text(encoding="utf-8"))
    names = Names(
        _rows(RAW / "tech_names_en.json"),
        items,
        _rows(RAW / "tables" / "en_DT_MapObjectNameText_Common.json"),
    )
    items_ci = {k.lower(): v for k, v in items.items()}

    entries, unresolved = [], []
    for tech_id in sorted(tech):
        row = tech[tech_id]
        name, source = names.tech(row.get("Name"))
        if name is None:
            unresolved.append(tech_id)
            name, source = tech_id, "tech_id"
        elif "<" in name:
            # A tag shape this ingest does not know about. Falling back is the point:
            # publishing raw markup as a technology name is the failure that made the
            # tag pattern case-insensitive, and the next unknown tag must not repeat it.
            unresolved.append(tech_id)
            name, source = tech_id, "tech_id"

        # What the technology actually gives you, by display name. A card that says
        # "Improved Furnace" is more use than one that says "BlastFurnace2", and both
        # names are in the tables already.
        grants = [names.map_object(o) or o for o in row.get("UnlockBuildObjects") or []]
        grants += [names.item(i) or i for i in row.get("UnlockItemRecipes") or []]

        prereq = _none(row.get("RequireTechnology"), NO_TECH)
        tower = _none(row.get("RequireDefeatTowerBoss"), NO_BOSS)
        entries.append({
            "tech_id": tech_id,
            "name": name,
            "name_source": source,
            # The PLAYER's level, as on a saddle. The one meaning of "level" on a Q6 card.
            "required_level": row.get("LevelCap"),
            "cost": row.get("Cost"),
            # Which pool pays. Never add the two together.
            "currency": "ancient" if row.get("IsBossTechnology") else "technology",
            "prerequisites": [prereq] if prereq else [],
            # The enum suffix, verbatim. See tower_join_note.
            "requires_tower": tower[len(BOSS_ENUM_PREFIX):] if tower else None,
            "requires_research": _none(row.get("RequireResearchId"), NO_RESEARCH),
            "category": category_of(row, items_ci),
            "unlocks": grants,
        })

    by_id = {e["tech_id"]: e for e in entries}
    ancient = [e for e in entries if e["currency"] == "ancient"]
    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "DT_TechnologyRecipeUnlock, with names resolved through "
                  "DT_TechnologyNameText_Common -> DT_ItemNameText_Common / "
                  "DT_MapObjectNameText_Common",
        "extraction_note": "Nothing here is derived except the tower join below. A "
                           "LevelCap, a Cost and an IsBossTechnology flag are columns.",
        "graph_note": "The prerequisite 'tree' is 17 edges over 588 rows, in six straight "
                      "chains, with every target present and no cycles. The real gate is "
                      "required_level, which every row carries and which spans 1-80. A "
                      "card must not present this as a tree to be climbed.",
        "currency_note": "cost is paid from ONE of two pools and they do not mix: "
                         "'ancient' spends the save's bossTechnologyPoint (Ancient "
                         "Technology Points, from tower bosses) and 'technology' spends "
                         "TechnologyPoint. Summing them would tell a player they can "
                         "afford something they cannot.",
        "tower_join_note": "requires_tower is the EPalBossType enum suffix, verbatim. "
                           "Checking it against a save joins it to the RecordData key "
                           "BOSS_BATTLE_NAME_<suffix>, which is an INFERENCE on the key "
                           "name - strong (all five flags present in the reference save "
                           "match a valid enum value) but stated nowhere. It is declared "
                           "here because a derived rule is a claim. Note also that these "
                           "suffixes do NOT line up with the leader regions in "
                           "bosses.json: the VOLCANO tower's gate is 'ElectricBoss', so "
                           "naming a gate by its leader needs a mapping the pak does not "
                           "state, and none is attempted.",
        "tier_note": "The table's Tier column is 0 on every row and is not published. A "
                     "column that never varies is not a category.",
        "name_note": "523 of 588 name rows are markup pointing at an item or a build "
                     "object; resolution is case-insensitive because the pak's casing is "
                     "not trustworthy on joins (third occurrence in this project). The "
                     "11 that resolve to nothing fall back to their tech_id and are "
                     "listed in unresolved_names - a rule that recovered five of them "
                     "(GrapplingGun -> ITEM_NAME_GrapplingGun_1) was NOT written, "
                     "because it would be a derived mapping worth less than it costs.",
        "granted_note": "PalBox appears in a save's UnlockedRecipeTechnologyNames and in "
                        "no row of this table, because it is granted rather than "
                        "researched. A join reporting one unmatched entry is correct.",
        "stats": {
            "technologies": len(entries),
            "with_prerequisites": sum(1 for e in entries if e["prerequisites"]),
            "ancient": len(ancient),
            "tower_gated": sum(1 for e in entries if e["requires_tower"]),
            "research_gated": sum(1 for e in entries if e["requires_research"]),
            "unresolved_names": len(unresolved),
            "level_min": min(e["required_level"] for e in entries),
            "level_max": max(e["required_level"] for e in entries),
            "categories": {c: sum(1 for e in entries if e["category"] == c)
                           for c in sorted({e["category"] for e in entries})},
        },
        "entries": entries,
        "unresolved_names": sorted(unresolved),
        "granted_not_researched": sorted(GRANTED_NOT_RESEARCHED),
        # Kept so a consumer can check a save's flags without re-deriving the enum set.
        "tower_gates": sorted({e["requires_tower"] for e in entries
                               if e["requires_tower"]}),
        "_graph_ok": _validate(by_id),
    }


def _validate(by_id: dict[str, dict]) -> bool:
    """Every prerequisite exists and nothing cycles. Raises rather than returning False.

    Worth doing even at 17 edges: the whole point of a candidate set is that
    `set(prerequisites) <= unlocked` is decidable, and a prerequisite naming a row that
    does not exist makes it permanently false - a technology that can never be
    recommended, with nothing on any card to say why.
    """
    for tech_id, entry in by_id.items():
        for p in entry["prerequisites"]:
            if p not in by_id:
                raise SystemExit(
                    f"ABORT: {tech_id} requires {p!r}, which is not a technology row.")
    for tech_id in by_id:
        seen, cursor = [], tech_id
        while cursor:
            if cursor in seen:
                raise SystemExit(f"ABORT: prerequisite cycle: {' -> '.join(seen)}")
            seen.append(cursor)
            prereqs = by_id[cursor]["prerequisites"]
            cursor = prereqs[0] if prereqs else None
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    needed = RAW / "tech_recipe_unlock.json"
    if not needed.exists():
        sys.exit(f"Missing {needed}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- tech")

    data = build(args.version)
    s = data["stats"]

    dest = REPO / "data" / args.version / "tech.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"tech -> {dest}")
    print(f"  technologies       {s['technologies']}"
          f"  (player lvl {s['level_min']}-{s['level_max']})")
    print(f"  currencies         {s['technologies'] - s['ancient']} technology"
          f"  /  {s['ancient']} ancient")
    print(f"  prerequisites      {s['with_prerequisites']} rows"
          f"   - the graph is 17 edges, not a tree")
    print(f"  gated on a tower   {s['tower_gated']}"
          f"   /  on lab research {s['research_gated']}")
    print(f"  categories         " + ", ".join(
        f"{c} {n}" for c, n in s["categories"].items()))
    if s["unresolved_names"]:
        print(f"  UNRESOLVED NAMES   {s['unresolved_names']}: "
              f"{', '.join(data['unresolved_names'][:4])}  (fell back to the tech id)")


if __name__ == "__main__":
    main()
