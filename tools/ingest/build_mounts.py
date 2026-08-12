"""Build the mount dataset — which Pals can be ridden, from what player level, how fast.

Input : data/raw/tables/DT_ItemDataTable.json   (the saddle items)
        data/raw/tech_recipe_unlock.json        (the saddle's technology, and its LevelCap)
        data/raw/pal_monster_parameter.json     (ride and swim speeds)
        data/<version>/lexicon.json             (internal id -> display name)
Output: data/<version>/mounts.json

**Everything here is stated by the game.** A saddle item exists or it does not, a
technology has a `LevelCap` or it does not, and a speed is an integer column. No prefix
rule, no key-suffix convention, nothing inferred.

**The saddle is the authority on rideability, not the speed field.** `RideSprintSpeed` is
populated on 693 of 753 rows and only 107 of those have a saddle, so a Pal having a ride
speed says nothing about whether you can ride it - the number simply never gets used.
Checked the other way round the join is clean: all 107 saddled Pals have a ride speed, so
no mount reaches a card without one.

**`-1` is a sentinel, not a speed.** 52-105 rows carry it depending on the field, mostly
humanoids and quest actors. It is dropped rather than stored, because a fastest-first sort
that kept it would rank "not applicable" against real numbers.

## Flying and ground mounts are one category here, and that is the data's doing

The pak has no flight flag. Seven candidate signals were measured against a hand-labelled
set on 2026-08-11 and all failed - `SwimSpeed == RunSpeed` (10/19 flyers),
`GenusCategory == Bird`, `PalFlyMeshHeightCtrlComponent` (2/6), the
`Pawn_NoDamageFlyPal` collision profile (2/6), `RidePositionType` (it is seat position,
not flight), fly-named animation assets (precision 6/6 but recall 6/12), and, decisively,
**the set of component classes present in every labelled flyer and no labelled ground Pal
is empty**. All 532 data tables in the pak were listed; none concerns movement.

That would only be a gap if the two had different speeds. They do not: a flyer's ridden
speed is `RideSprintSpeed`, the same column a ground mount uses, and there is no separate
flight-speed field anywhere. So "fastest flying mount" and "fastest ground mount" are the
same question of this data, and grouping them is reporting what the game distinguishes
rather than papering over what it does not. The card says so.

Water is genuinely separate, because `SwimDashSpeed` is a different column.

Usage: python tools/ingest/build_mounts.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

SADDLE_PREFIX = "SkillUnlock_"
# Pal Gear covers saddles, gloves, chokers and mounted weapons. Only the saddle icon means
# "you can ride this" - `SkillUnlock_Gloves` is Hedgehog's throwing gear and
# `SkillUnlock_Minigun` is a weapon platform, and both would otherwise land in a list of
# mounts.
SADDLE_ICON = "SkillUnlock_Saddle"
PAL_GEAR = "EPalItemTypeB::Essential_PalGear"
# The value the table uses for "this Pal has no such movement", distinct from a speed of
# zero, which no row actually carries.
NOT_APPLICABLE = -1


def _rows(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc = doc[0] if isinstance(doc, list) else doc
    return doc.get("Rows", doc)


def speed(row: dict, field: str) -> int | None:
    v = row.get(field)
    if not isinstance(v, (int, float)) or v == NOT_APPLICABLE or v <= 0:
        return None
    return int(v)


def build(version: str) -> dict:
    items = _rows(RAW / "tables" / "DT_ItemDataTable.json")
    tech = _rows(RAW / "tech_recipe_unlock.json")
    pals = _rows(RAW / "pal_monster_parameter.json")
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    name_of = {i.lower(): p["canonical"]
               for p in lexicon["pals"] for i in p["internal_ids"]}

    saddles = {k for k, v in items.items()
               if v.get("TypeB") == PAL_GEAR and v.get("IconName") == SADDLE_ICON}

    # saddle item -> the player level its technology unlocks at. Read from the tech row
    # rather than from the item, because the item's own `TechnologyTreeLock` is 0 on every
    # saddle and would have published "unlocks at level 0" for all 108.
    unlock: dict[str, int] = {}
    for row in tech.values():
        cap = row.get("LevelCap")
        for recipe in (row.get("UnlockItemRecipes") or []):
            if recipe in saddles and isinstance(cap, int):
                unlock[recipe] = cap

    # Case-insensitive, and that is not defensive coding - it is a bug this build had.
    # The item is `SkillUnlock_Thunderdog_Ice` and the stat row is `ThunderDog_Ice`, one
    # capital letter apart, so an exact lookup silently dropped Rayhound Cryst from the
    # mount roster. Exactly the trap `build_bosses.py` records for `Boss_Anubis`, the one
    # row in 323 not spelled `BOSS_`. Two occurrences in two datasets means the pak's
    # casing is not to be trusted on any join.
    stats_by_id = {k.lower(): (k, v) for k, v in pals.items()}

    entries, unjoined, unlevelled = [], [], []
    for item_id in sorted(saddles):
        tribe = item_id[len(SADDLE_PREFIX):]
        found = stats_by_id.get(tribe.lower())
        stats = found[1] if found else None
        # The stat table's spelling wins: it is what every other dataset keys on, so a
        # card built from this one and a card built from elsewhere name the same row.
        tribe = found[0] if found else tribe
        name = name_of.get(tribe.lower())
        if stats is None or not name:
            # A saddle for something with no stat row or no player-facing name. Recorded
            # rather than dropped silently: it means the item table and the Pal table
            # disagree about what exists, which is worth seeing on a patch.
            unjoined.append(item_id)
            continue
        level = unlock.get(item_id)
        if level is None:
            unlevelled.append(item_id)
        entries.append({
            "character_id": tribe,
            "name": name,
            "saddle_item": item_id,
            # The PLAYER's level, not the Pal's. This is the whole reason "what mount can
            # I get at 60" means something different from "what Pal is level 60", and
            # STATUS's 2026-08-11 decision is amended around exactly this field.
            "unlock_level": level,
            # Ridden speed on land - and in the air. There is no flight-speed column; see
            # the module docstring.
            "ride_speed": speed(stats, "RideSprintSpeed"),
            "swim_speed": speed(stats, "SwimDashSpeed"),
            "cruise_swim_speed": speed(stats, "SwimSpeed"),
        })

    by_level = [e["unlock_level"] for e in entries if e["unlock_level"] is not None]
    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "DT_ItemDataTable Essential_PalGear/SkillUnlock_Saddle + "
                  "DT_TechnologyRecipeUnlock LevelCap + DT_PalMonsterParameter speeds",
        "extraction_note": "Nothing here is derived. A saddle item exists or it does "
                           "not, its technology has a LevelCap or it does not, and a "
                           "speed is an integer column.",
        "authority_note": "The SADDLE decides rideability, never the speed field. "
                          "RideSprintSpeed is populated on 693 of 753 rows and only 107 "
                          "of those have a saddle, so a ride speed says nothing about "
                          "whether a Pal can be ridden - the number just never gets "
                          "used. The reverse holds cleanly: every saddled Pal has one.",
        "level_note": "unlock_level is the PLAYER's level, from the saddle's technology. "
                      "It is not the Pal's level and must never be rendered as one - the "
                      "product already prints 'lvl 68-72' meaning the Pal on spawn cards.",
        "medium_note": "Flying and ground mounts are ONE category here because the pak "
                       "does not separate them: there is no flight flag (seven signals "
                       "measured and falsified 2026-08-11, including an empty "
                       "component-class difference between labelled flyers and ground "
                       "Pals) and, more to the point, no flight SPEED - a flyer's ridden "
                       "speed is RideSprintSpeed, the same column a ground mount uses. "
                       "Water is separate because SwimDashSpeed is a separate column.",
        "sentinel_note": "-1 means 'not applicable', not a speed, and is stored as null. "
                         "Keeping it would rank 'no such movement' above real numbers in "
                         "a fastest-first sort.",
        "casing_note": "The saddle-to-stat join is CASE-INSENSITIVE. SkillUnlock_"
                       "Thunderdog_Ice against the stat row ThunderDog_Ice is one "
                       "capital letter, and an exact lookup silently dropped Rayhound "
                       "Cryst from the roster. Second time in this project after "
                       "Boss_Anubis (see bosses.json casing_note), so the pak's casing "
                       "is not trustworthy on any join.",
        "unlevelled_note": "A saddle with no technology row has unlock_level null, and a "
                           "null must never be read as 'available now'. Two exist "
                           "(Boltmane, Broncherry Aqua): the item is real and nothing in "
                           "DT_TechnologyRecipeUnlock unlocks it, so how you get them is "
                           "genuinely unknown from the pak. A player-level filter EXCLUDES "
                           "them and the card says how many it could not check.",
        "stats": {
            "saddles": len(saddles),
            "mounts": len(entries),
            "with_unlock_level": len(by_level),
            "unlock_level_min": min(by_level) if by_level else None,
            "unlock_level_max": max(by_level) if by_level else None,
            "with_ride_speed": sum(1 for e in entries if e["ride_speed"]),
            "with_swim_speed": sum(1 for e in entries if e["swim_speed"]),
            "unjoined_saddles": len(unjoined),
            "saddles_without_a_level": len(unlevelled),
        },
        "entries": sorted(entries, key=lambda e: e["name"]),
        "unjoined_saddles": sorted(unjoined),
        "saddles_without_a_level": sorted(unlevelled),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    needed = RAW / "tables" / "DT_ItemDataTable.json"
    if not needed.exists():
        sys.exit(f"Missing {needed}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- tables")

    data = build(args.version)
    s = data["stats"]
    # Every mount must have a ride speed. The join was measured clean at 107/107, and a
    # mount with no speed would sort as "unknown" against real numbers on a card whose
    # entire purpose is ranking by speed.
    if s["with_ride_speed"] != s["mounts"]:
        sys.exit(f"{s['mounts'] - s['with_ride_speed']} mount(s) have no ride speed - "
                 f"the saddle table and the stat table disagree. Nothing was written.")

    dest = REPO / "data" / args.version / "mounts.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"mounts -> {dest}")
    print(f"  mounts             {s['mounts']}  of {s['saddles']} saddle items"
          + (f"  ({s['unjoined_saddles']} unjoined)" if s["unjoined_saddles"] else ""))
    print(f"  unlock levels      {s['with_unlock_level']}"
          f"  (player lvl {s['unlock_level_min']}-{s['unlock_level_max']})")
    print(f"  ride / swim speed  {s['with_ride_speed']} / {s['with_swim_speed']}")
    if s["saddles_without_a_level"]:
        print(f"  NO UNLOCK LEVEL    {s['saddles_without_a_level']}: "
              f"{', '.join(data['saddles_without_a_level'][:4])}")
    if data["unjoined_saddles"]:
        print(f"  unjoined: {', '.join(data['unjoined_saddles'][:4])}")


if __name__ == "__main__":
    main()
