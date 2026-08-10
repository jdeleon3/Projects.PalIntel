"""Build the resource -> "which Pals drop this" dataset.

Input : data/raw/pal_drops.json   (DT_PalDropItem_Common, keyed by CharacterID)
        data/raw/items.json, node_drops.json  (via _resources, for the item ids)
        data/<version>/lexicon.json
Output: data/<version>/pal_drops.json

This answers a question the coordinates cannot: coal is also a thing you can farm off a
Blazamut, and a player who cannot yet survive a level-40 mining spot may be able to.

Two judgements are made here, both recorded on the published dataset because both change
what the card's line MEANS.

**Rate-0 rows are dropped.** The table carries real rows with `Rate: 0` - Smokie Cryst
for coal, Neptilius for quartz, Tetroise for sulfur. Whatever they are upstream, they are
not drops that happen, and printing them would put a Pal on a card that never yields the
item. That is the fabricated-value failure in a slot the player would act on.

**Boss variants are collapsed to their base species, and this is an INFERENCE.** The
table keys `BOSS_RockBeast` separately from `RockBeast`, and reading the prefix as "the
alpha of" is a rule derived from the naming, not something the data states. It matters
because it changes the claim: where only a `BOSS_` row exists, "Blazamut drops coal" is
true of the alpha and may not be true of an ordinary one. `alpha_only` is published per
dropper so a card can say so rather than implying otherwise.

Usage: python tools/ingest/build_pal_drops.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _resources import item_ids  # noqa: E402

# Untranslated rows. The game ships placeholder names on cut or unused variants
# (AnimalSkin2, Scales, Claws2, Fang2, Leather2), and "en Text" is not a name - letting
# one through would put an unaskable entity in the item vocabulary.
PLACEHOLDER_NAMES = {"", "None", "en Text", "ja_text", "en_text"}

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Prefixes the table uses for the harder versions of a species. RAID and SUMMON are
# included for completeness; neither currently contributes a resource dropper.
VARIANT_PREFIX = re.compile(r"^(BOSS|PREDATOR|RAID|SUMMON)_")

# Scenario-mode suffixes. These are not encounters a player farms in the world, and they
# share a base name with a real Pal - so stripping the suffix would credit that Pal with a
# drop only its quest or boss-rush version has. `_BossRush` joined the list once widening
# to all items surfaced BlackGriffon, BlueSkyDragon, ElecPanda and Horus arriving as
# unresolved ids; excluding them is a decision, and it belongs here rather than in the
# residue of a name lookup that happened to fail.
QUEST_SUFFIX = re.compile(r"_(Quest|Quest_Enemy|Quest_Friend|Avatar|BossRush)$")

# How many to name on a card before it stops being a line and becomes a list. Ore has 8
# droppers; the card shows the best three and says how many more there are.
MAX_NAMED = 3


def build(version: str) -> dict:
    rows = json.loads((RAW / "pal_drops.json").read_text(encoding="utf-8"))
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    # Case-insensitive, because the drop table's own casing is inconsistent with the
    # name table's: it carries `Gorilla_ground`, `KingBahamut_dragon`, `SkyDragon_grass`
    # and `Drillgame` against the lexicon's `Gorilla_Ground`, `KingBahamut_Dragon`,
    # `SkyDragon_Grass` and `DrillGame`. Matching exactly silently dropped five real
    # droppers as unresolvable ids.
    by_internal = {i.lower(): p["canonical"]
                   for p in lexicon["pals"] for i in p["internal_ids"]}
    items = json.loads((RAW / "items.json").read_text(encoding="utf-8"))

    def display(item_id: str) -> str | None:
        name = (items.get(item_id) or {}).get("name")
        return None if name in PLACEHOLDER_NAMES or name is None else name

    resources = item_ids()
    wanted = {item: res for res, item in resources.items()}

    # item -> canonical Pal -> best rate seen, and whether an ordinary row ever carried it
    found: dict[str, dict[str, dict]] = defaultdict(dict)
    unresolved: set[str] = set()
    zero_rate = 0

    for row in rows:
        cid = row["character_id"] or ""
        if QUEST_SUFFIX.search(cid):
            continue
        is_variant = bool(VARIANT_PREFIX.match(cid))
        base = VARIANT_PREFIX.sub("", cid)
        pal = by_internal.get(cid.lower()) or by_internal.get(base.lower())

        for drop in row["drops"]:
            # Every named item now, not just the 18 with map nodes. The card view is
            # derived from this below; narrowing here would mean two passes with two
            # chances to apply the rules differently.
            if display(drop["item"]) is None:
                continue
            if pal is None:
                unresolved.add(cid)
                continue
            if drop["rate"] <= 0:
                zero_rate += 1
                continue
            entry = found[drop["item"]].setdefault(
                pal, {"pal": pal, "rate": 0.0, "min": 0, "max": 0, "alpha_only": True})
            entry["rate"] = max(entry["rate"], drop["rate"])
            entry["min"] = max(entry["min"], drop["min"])
            entry["max"] = max(entry["max"], drop["max"])
            if not is_variant:
                entry["alpha_only"] = False

    def ranked(droppers: dict) -> list[dict]:
        # Best rate first, then the bigger haul, then by name so the order is stable
        # across rebuilds - a card that reshuffles between patches for no reason is
        # noise the reader has to re-scan.
        return sorted(droppers.values(), key=lambda d: (-d["rate"], -d["max"], d["pal"]))

    # Three views, one pass, one set of rules. Deriving the card's resource view from the
    # item view rather than collecting it separately is what stops the "also drops from"
    # line and the item query ever disagreeing about the same fact.
    by_item = {display(item): ranked(d) for item, d in found.items()}
    by_resource = {wanted[item]: ranked(d) for item, d in found.items()
                   if item in wanted}

    by_pal: dict[str, list[dict]] = defaultdict(list)
    for item, droppers in found.items():
        for d in droppers.values():
            by_pal[d["pal"]].append({"item": display(item), "rate": d["rate"],
                                     "min": d["min"], "max": d["max"],
                                     "alpha_only": d["alpha_only"]})
    for pal, drops in by_pal.items():
        drops.sort(key=lambda d: (-d["rate"], -d["max"], d["item"]))

    return {
        "dataset_version": 2,
        "game_version": version,
        "source": "DT_PalDropItem_Common, extracted from Pal-Windows.pak",
        "rules": {
            "rate_zero": "excluded - the table carries rows with Rate 0, and naming a "
                         "Pal that never yields the item is a fabricated value",
            "variant_collapse": "BOSS_/PREDATOR_/RAID_/SUMMON_ rows are credited to the "
                                "base species. This is DERIVED from the naming, not "
                                "stated by the data; alpha_only marks a dropper seen "
                                "only on a variant row",
            "scenario_actors": "rows suffixed _Quest/_Avatar/_BossRush are excluded - "
                               "they share a base name with a real Pal and are not "
                               "encounters a player farms in the world",
            "placeholder_names": "items whose English name is a placeholder (\"en Text\") "
                                 "are excluded - an unaskable entity in the vocabulary",
        },
        "stats": {
            "items_with_droppers": len(by_item),
            "pals_that_drop_something": len(by_pal),
            "resources_with_droppers": len(by_resource),
            "resources_total": len(resources),
            "zero_rate_rows_excluded": zero_rate,
            "character_ids_unresolved": len(unresolved),
        },
        "max_named_on_card": MAX_NAMED,
        "by_item": dict(sorted(by_item.items())),
        "by_pal": dict(sorted(by_pal.items())),
        "by_resource": dict(sorted(by_resource.items())),
        "unresolved_character_ids": sorted(unresolved)[:40],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    if not (RAW / "pal_drops.json").exists():
        sys.exit("No data/raw/pal_drops.json.\n"
                 "  Run: dotnet run --project tools/extract/PakExtract -- paldrops")

    data = build(args.version)
    dest = REPO / "data" / args.version / "pal_drops.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"pal drops -> {dest}")
    print(f"  items with droppers      {s['items_with_droppers']}")
    print(f"  pals that drop something {s['pals_that_drop_something']}")
    print(f"  resources with droppers  {s['resources_with_droppers']} of {s['resources_total']}")
    print(f"  rate-0 rows excluded     {s['zero_rate_rows_excluded']}")
    print(f"  unresolved CharacterIDs  {s['character_ids_unresolved']}")
    print()
    for res, droppers in data["by_resource"].items():
        named = ", ".join(f"{d['pal']}{'*' if d['alpha_only'] else ''}"
                          for d in droppers[:MAX_NAMED])
        more = f"  +{len(droppers) - MAX_NAMED}" if len(droppers) > MAX_NAMED else ""
        print(f"  {res:<18}{named}{more}")
    print("\n  * alpha only")


if __name__ == "__main__":
    main()
