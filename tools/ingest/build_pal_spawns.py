"""Build the Pal spawn dataset from extracted placements and spawner sheet tables.

Input : data/raw/placements.json            (BP_PalSpawner_Sheets_* actors, world coords)
        data/raw/spawner_sheets.json        (each sheet's SpawnGroupList: species, level,
                                             weight)
        data/raw/boss_spawner_locations.json (DT_BossSpawnerLoactionData - field alphas)
        data/coord_transform.json           (world -> in-game map)
        data/<version>/lexicon.json         (internal id -> canonical name)
Output: data/<version>/pal_spawns.json

The first two inputs answer different halves of the question and neither is useful alone:
the cell scan says a `BP_PalSpawner_Sheets_green_K_C` actor stands at (312, -88), and the
sheet table says that class rolls Chikipi, Lamball or Cattiva at level 1-3. Joining them
on the class name is what produces "Chikipi is over there".

Field alphas come from BOTH the sheet actors and the boss data table, because neither
source contains the other. The table knows 16 species the sheet actors do not (Penking,
Wixen, Blazehowl and thirteen more); the sheet actors know three the table does not
(Necromus, Broncherry Aqua, Ribbuny Botan). Merging the point sets and letting the
clustering deduplicate them is safe precisely because the two agree so closely where they
overlap - median disagreement 0.0 map units over 74 shared spawners, p90 0.1 - so a
duplicate collapses into one area while a genuine second location survives as its own.
Two do survive, correctly: Caprity Noct and Foxparks Cryst have a low-level main-island
alpha in the table and a separate level-50s one on Feybreak in the sheets.

Usage: python tools/ingest/build_pal_spawns.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from _cluster import anchor, cluster, spread

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Cluster radius in map units. 1 map unit is about 4.6 m, so 25 units is a ~115 m radius:
# roughly the distance over which a wandering spawn is still "here" rather than "over
# there". Larger than the 12 used for resource nodes, deliberately - a deposit is a
# visible object you walk to, a spawn point is a region you stand in and wait.
#
# Chosen by sweeping against known spots rather than by feel. At 25 the desert Anubis
# resolves to one dominant 17-point area plus scatter; at 40 it merges distinct hillsides
# into 370 m blobs (Chikipi's largest area goes 60 -> 91 points and stops being a place).
CLUSTER_RADIUS = 25.0

# Spawn entries whose PalId is one of these are not Pals.
#   None    - a human NPC spawn; the species is in NPCID instead (Hunter_Handgun, Viking)
#   RowName - an unfilled placeholder row, always paired with Weight 0
NON_PAL_IDS = frozenset({"None", "RowName"})

# Internal ids with no entry in the game's own name table, mapped by hand. PlantSlime_Flower
# is the flower variant of Gumoss and the game gives it no separate display name, so folding
# it into the base Pal is what the game itself does.
ID_ALIASES = {"plantslime_flower": "PlantSlime"}

# BOSS_ and PREDATOR_ prefix a base species id. They are kept as separate `kind`s rather
# than folded in, because "where's the alpha Chillet" and "where do Chillet spawn" have
# different answers - one is a fixed named encounter, the other is a region.
_KIND_PREFIX = re.compile(r"^(BOSS|PREDATOR)_", re.I)
_QUEST_SUFFIX = re.compile(r"_Quest$", re.I)
KIND_NAMES = {"BOSS": "alpha", "PREDATOR": "predator"}

# PvP arena spawners. Excluded, and the exclusion is load-bearing rather than tidy-up:
# `BP_PalSpawner_Sheets_PvP_21_1_1_C` and its sibling are placed 1,113 times across the
# whole map, and they carry the common early-game species - 83% of every Rushoar spawn
# point, 73% of every Chikipi. Left in, they flatten the density signal that makes
# "nearest" and "best place" mean anything, for exactly the Pals a new player asks about.
#
# It costs no coverage: no Pal reaches the dataset through a PvP sheet alone. Whether
# these spawners are live during normal play is NOT established here - it is recorded as
# a validation item, and excluding them is the reading that cannot invent a location.
EXCLUDED_SHEET_PATTERNS = ("PvP",)

# A cluster spans at most 2*CLUSTER_RADIUS. Unlike resource deposits there is no natural
# ceiling on spawner density, so a raw count cannot detect a bad extraction here. What
# does detect it is zero spread across many points: that is coordinates collapsing to a
# single value, which is what an unresolved parent transform looks like.
MIN_SPREAD_POINTS = 8


def boss_table_points(by_id: dict[str, str], transform: dict,
                      unmapped: defaultdict[str, int]) -> list[dict]:
    """Field alpha spawners from DT_BossSpawnerLoactionData, in placement shape.

    Rows carry an exact world position and an exact level rather than a range, so these
    are the better record wherever they exist. 69 of the 159 rows have `CharacterID:
    "None"` - unfilled slots, not spawners - and are skipped.

    Note the boss table is also what the coordinate transform was fitted against
    (data/coord_transform.json). That makes it authoritative for position here, and it is
    why the agreement with the sheet actors measures the actor extraction and its owner-
    chain composition rather than the transform, which cancels out of both sides.
    """
    m = transform["model"]
    scale, ox, oy = m["scale"], m["offset_x"], m["offset_y"]
    rows = json.loads((RAW / "boss_spawner_locations.json").read_text("utf-8"))["Rows"]

    out = []
    for row in rows.values():
        char = row["CharacterID"]
        if char == "None":
            continue
        base = _QUEST_SUFFIX.sub("", _KIND_PREFIX.sub("", char))
        canon = by_id.get(ID_ALIASES.get(base.lower(), base).lower())
        if canon is None:
            unmapped[char] += 1
            continue
        loc = row["Location"]
        out.append({
            "cls": f"DT_BossSpawner:{row['SpawnerID']}",
            "world_x": loc["X"], "world_y": loc["Y"], "world_z": loc["Z"],
            "map_x": round((loc["Y"] - oy) / scale, 1),
            "map_y": round((loc["X"] - ox) / scale, 1),
            "_pal": canon,
            "_profile": {"share": 1.0, "level_min": row["Level"],
                         "level_max": row["Level"], "night_only": False},
        })
    return out


def canonical_id(key: str) -> tuple[str, str] | None:
    """Split a spawn entry's PalId into (base internal id, kind), or None if not a Pal."""
    if key in NON_PAL_IDS:
        return None
    m = _KIND_PREFIX.match(key)
    kind = KIND_NAMES[m.group(1).upper()] if m else "normal"
    base = _QUEST_SUFFIX.sub("", key[m.end():] if m else key)
    return ID_ALIASES.get(base.lower(), base), kind


def sheet_profiles(sheets: dict[str, dict], by_id: dict[str, str],
                   unmapped: defaultdict[str, int],
                   inverted: list[str]) -> dict[str, dict]:
    """Per sheet: what each (pal, kind) rolls there, and how often.

    `share` is the sheet's weight for this Pal over its total weight - the chance that a
    spawner of this class produces this species at all. It is the difference between "one
    of the three things here" and "a 1-in-300 roll", and a card that cannot say which is
    telling the player to camp a spot they will never see the Pal at.
    """
    out: dict[str, dict] = {}
    for cls, sheet in sheets.items():
        groups = [g for g in sheet["spawn_group_list"] if g["Weight"] > 0]
        total = sum(g["Weight"] for g in groups)
        if total == 0:
            continue
        profile: dict[tuple[str, str], dict] = {}
        for g in groups:
            night = g["OnlyTime"].endswith("Night")
            for entry in g["PalList"]:
                split = canonical_id(entry["PalId"]["Key"])
                if split is None:
                    continue
                base, kind = split
                canon = by_id.get(base.lower())
                if canon is None:
                    unmapped[entry["PalId"]["Key"]] += 1
                    continue
                p = profile.setdefault((canon, kind), {
                    "weight": 0, "level_min": 999, "level_max": 0, "night_only": True})
                # Two entries in the shipped game data have Level > Level_Max - a
                # designer typo on Pengullet in two snow sheets. Sorting rather than
                # trusting the field names keeps a 35-34 range from becoming a record
                # that claims a minimum above its own maximum.
                lo, hi = sorted((entry["Level"], entry["Level_Max"]))
                if lo != entry["Level"]:
                    inverted.append(f"{cls}:{entry['PalId']['Key']} "
                                    f"{entry['Level']}-{entry['Level_Max']}")
                p["weight"] += g["Weight"]
                p["level_min"] = min(p["level_min"], lo)
                p["level_max"] = max(p["level_max"], hi)
                p["night_only"] = p["night_only"] and night
        for p in profile.values():
            p["share"] = p["weight"] / total
        out[cls] = profile
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    ap.add_argument("--radius", type=float, default=CLUSTER_RADIUS)
    ap.add_argument("--include-pvp", action="store_true",
                    help="keep PvP arena sheets (see EXCLUDED_SHEET_PATTERNS)")
    args = ap.parse_args()

    lex = json.loads((REPO / "data" / args.version / "lexicon.json").read_text("utf-8"))
    by_id = {i.lower(): p["canonical"] for p in lex["pals"] for i in p["internal_ids"]}
    all_pals = {p["canonical"] for p in lex["pals"]}

    sheets = {s["cls"]: s for s in
              json.loads((RAW / "spawner_sheets.json").read_text("utf-8"))}
    placements = [p for p in json.loads((RAW / "placements.json").read_text("utf-8"))
                  if p["kind"] == "pal_spawn"]

    excluded = 0
    if not args.include_pvp:
        keep = [p for p in placements
                if not any(x in p["cls"] for x in EXCLUDED_SHEET_PATTERNS)]
        excluded = len(placements) - len(keep)
        placements = keep

    unmapped: defaultdict[str, int] = defaultdict(int)
    inverted: list[str] = []
    profiles = sheet_profiles(sheets, by_id, unmapped, inverted)

    missing_sheets = sorted({p["cls"] for p in placements if p["cls"] not in profiles})
    if missing_sheets:
        raise SystemExit(
            f"ABORT: {len(missing_sheets)} placed spawner class(es) have no sheet table, "
            f"e.g. {missing_sheets[:3]}. Re-run `dotnet run -- sheets`.")

    # Every placement of a sheet is a spawn point for each species that sheet rolls.
    points: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in placements:
        for (canon, kind), prof in profiles[p["cls"]].items():
            points[(canon, kind)].append(p | {"_profile": prof})

    transform = json.loads((REPO / "data" / "coord_transform.json").read_text("utf-8"))
    bosses = boss_table_points(by_id, transform, unmapped)
    for b in bosses:
        points[(b["_pal"], "alpha")].append(b)

    print(f"placements: {len(placements):,} kept, {excluded:,} excluded "
          f"({len(profiles):,} sheet tables); {len(bosses):,} boss table rows")
    if unmapped:
        print(f"\n{len(unmapped)} internal id(s) with no lexicon entry, excluded:")
        for k, n in sorted(unmapped.items(), key=lambda kv: -kv[1])[:10]:
            print(f"   {k:<28} {n:>4} entries")
    if inverted:
        print(f"\n{len(inverted)} inverted level range(s) in the source data, sorted:")
        for i in inverted:
            print(f"   {i}")

    records = []
    for (canon, kind), pts in sorted(points.items()):
        groups = cluster(pts, args.radius)
        groups.sort(key=len, reverse=True)
        for gi, g in enumerate(groups):
            at = anchor(g)
            profs = [p["_profile"] for p in g]
            # Point-weighted mean: a cluster mixing a dedicated sheet with the edge of a
            # broad one is genuinely less reliable than the dedicated sheet alone, and
            # taking the max would report the good case and hide the mixture.
            share = sum(p["share"] for p in profs) / len(profs)
            records.append({
                "area_id": f"{canon.lower().replace(' ', '_')}_{kind}_{gi:03d}",
                "pal": canon,
                "kind": kind,
                "map_x": at["map_x"],
                "map_y": at["map_y"],
                "spawn_points": len(g),
                "spread_map_units": round(spread(g, at["map_x"], at["map_y"]), 1),
                "level_min": min(p["level_min"] for p in profs),
                "level_max": max(p["level_max"] for p in profs),
                "night_only": all(p["night_only"] for p in profs),
                "encounter_share": round(share, 4),
                "world": {"x": at["world_x"], "y": at["world_y"], "z": at["world_z"]},
                "transform_id": transform["transform_id"],
                "sheets": sorted({p["cls"] for p in g}),
            })

    with_areas = {r["pal"] for r in records}
    out = {
        "game_version": args.version,
        "source": "BP_PalSpawner_Sheets_* actors in PL_MainWorld5 cells, joined to each "
                  "sheet blueprint's SpawnGroupList",
        "scope": "overworld only - dungeons, raids and instanced maps are NOT included",
        "cluster_radius_map_units": args.radius,
        "transform_id": transform["transform_id"],
        "known_gaps": [
            f"{len(all_pals - with_areas)} Pals have no overworld spawn area; they are "
            "dungeon, tower, raid or breeding-only. Listed in pals_without_areas so the "
            "answer can be 'not in the overworld' rather than 'not found'",
            "PvP arena sheets are excluded - whether they are live in normal play is "
            "unvalidated, and they dominate density for common early Pals"
            if not args.include_pvp else "PvP arena sheets INCLUDED (--include-pvp)",
            "encounter_share is the sheet's weight share, not an observed spawn rate; it "
            "has not been checked against in-game encounter frequency. Alpha areas from "
            "the boss data table carry share 1.0 because they are a fixed encounter, not "
            "a roll",
        ],
        "stats": {
            "spawn_points": len(placements),
            "areas": len(records),
            "pals_with_areas": len(with_areas),
            "by_kind": {k: sum(1 for r in records if r["kind"] == k)
                        for k in sorted({r["kind"] for r in records})},
        },
        "pals_without_areas": sorted(all_pals - with_areas),
        "areas": sorted(records, key=lambda r: (r["pal"], r["kind"], -r["spawn_points"])),
    }

    # Fail closed: a silently wrong dataset produces confidently wrong cards.
    collapsed = [r for r in records
                 if r["spawn_points"] >= MIN_SPREAD_POINTS and r["spread_map_units"] == 0.0]
    if collapsed:
        for r in collapsed[:5]:
            print(f"  IMPLAUSIBLE: {r['area_id']} has {r['spawn_points']} spawn points "
                  f"at zero spread, all at ({r['map_x']}, {r['map_y']})")
        raise SystemExit(
            f"\nABORT: {len(collapsed)} area(s) have many points and no spread. That is "
            "coordinates collapsing to a value, not a real spawn cluster. Not publishing.")

    # The guarantee is on the DIAMETER, not the radius: every member is within `radius`
    # of the seed, and the reported coordinate is the member nearest the centroid rather
    # than the seed itself, so spread measured from it is bounded by 2*radius. Asserting
    # the radius instead fails on 735 perfectly good areas.
    over = [r for r in records if r["spread_map_units"] > 2 * args.radius + 1e-6]
    if over:
        raise SystemExit(f"\nABORT: {len(over)} area(s) exceed the cluster diameter - the "
                         "clustering guarantee is broken, not the data.")

    dest = REPO / "data" / args.version / "pal_spawns.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"\nareas: {len(records):,} across {len(with_areas)} Pals  "
          f"({out['stats']['by_kind']})")
    print(f"no overworld spawn: {len(all_pals - with_areas)} Pals")
    print(f"\n-> {dest}")

    print("\ntop Anubis areas:")
    for r in [r for r in out["areas"] if r["pal"] == "Anubis"][:5]:
        print(f"   {r['kind']:<9} {r['spawn_points']:>3} pts at "
              f"({r['map_x']:>7.0f}, {r['map_y']:>7.0f})  lv {r['level_min']}-"
              f"{r['level_max']}  share {r['encounter_share']:.0%}")


if __name__ == "__main__":
    main()
