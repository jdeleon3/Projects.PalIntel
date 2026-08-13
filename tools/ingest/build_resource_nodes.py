"""Build the resource node dataset from extracted World Partition placements.

Input : data/raw/placements.json   (BP_PalMapObjectSpawner_* actors, world + map coords)
        data/raw/node_drops.json   (spawner class -> the item it actually yields)
        data/raw/items.json        (item id -> English name and category)
Output: data/<version>/resource_nodes.json

Individual spawner actors are single deposits. Players think in *clusters* - "the coal
spot at -160,-84" means a group of adjacent deposits - so actors are grouped into
clusters with an explicit node_count, matching ResourceNode in Docs/02-data-model.md.

Usage: python tools/ingest/build_resource_nodes.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from _cluster import anchor, cluster, spread
from _resources import derive, provided

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Spawner class -> resource is derived, not hand-written; the rule and the reason live
# in _resources.py, shared with the lexicon build so the two cannot disagree about what
# a resource is.

# Only spatially-partitioned cells are overworld. Everything else is a dungeon interior,
# and its coordinates are NOT overworld coordinates.
#
# World Partition names its streaming cells `MainGrid_L<level>_X<x>_Y<y>_DL<hash>`, and
# the grid position in that name is real for L0 - it is where the cell sits in the world.
# Cave and dungeon contents live at `L15_X0_Y0`, one cell at the grid origin holding all
# 6,719 of them, because they are authored in their own local space rather than placed on
# the map. Run through the overworld transform their positions are meaningless: about
# half land in open water, and the other half land on unrelated terrain, which is worse
# because nothing looks wrong about them.
#
# This cost 16.4% of the node dataset - 6,718 of 40,968 - and shipped through the whole
# of Phase 1 and 2 as answerable overworld coordinates. The published `scope` field has
# claimed "overworld only" since the dataset was created; this is what makes it true.
# The hazard is named in Docs/03-data-ingestion.md section 3.1 ("Overworld and
# instanced-dungeon nodes must be distinguished explicitly") and was simply never
# enforced. It was invisible until a map crop drew a coal marker in the sea.
CELL_GRID = re.compile(r"_L(\d+)_X(-?\d+)_Y(-?\d+)")


def is_overworld(placement: dict) -> bool:
    """Whether a placement's coordinate is a place on the overworld map.

    The cell level is most of the answer but not all of it. `L15_X0_Y0` holds dungeon
    contents whose RelativeLocation is authored in their own space - but **633 of its
    actors carry an Owner**, and composing that parent transform puts them back in world
    space. Measured against the basemap they land on terrain 76.5% of the time, against
    48,144 L0 placements at 79.4% and the 6,086 unresolved L15 ones at 46.3%. They are
    ordinary overworld nodes that happen to be authored inside a placement volume.

    Excluding them wholesale was a real miss, caught by standing on one: a card said the
    nearest coal was at (198, -231) and there was coal at (230, -218), which is
    `MainGrid_L15_X0_Y0` placement (230.7, -217.0) with `owner_hops=1`.

    This is the second time this project has over-corrected in the same direction. Phase 1
    dropped every deposit within 2,000 world units of the origin and cost 152 real coal
    deposits; the fix was to compose owner chains, which is precisely what rescued these
    633 - and then this filter discarded them again.
    """
    match = CELL_GRID.search(placement["cell"])
    # An unparseable cell name is excluded rather than assumed overworld: a naming change
    # upstream should cost coverage loudly, not silently readmit dungeon coordinates.
    if not match:
        return False
    if match.group(1) == "0":
        return True
    # Non-L0 and owner-resolved: the parent transform is a world transform, so this is a
    # world position. Unresolved, it is a dungeon-local coordinate and meaningless here.
    return placement.get("owner_hops", 0) > 0

# Region hints. These two used to carry the whole distinction, because both classes were
# mapped to `ore` and the hint was the only thing saying which one you had found. They
# are now separate resources (Soralite and Paloxite), so the hint is what it always
# should have been: where the node is, not what it is.
CLASS_TO_AREA_HINT = {
    "BP_PalMapObjectSpawner_SkyIslandOre_C": "sky_island",
    "BP_PalMapObjectSpawner_WorldTreeOre_C": "world_tree",
}

# Cluster radius in map units. 1 map unit is about 4.6 m, so 12 units is roughly 55 m -
# a group a player can see from its centre. Leader clustering bounds cluster DIAMETER
# at 2x this value.
CLUSTER_RADIUS = 12.0


# A cluster spans at most 2*CLUSTER_RADIUS (~110 m), and a very dense one is suspicious:
# it can mean coordinates collapsed to a point, which is what an unresolved parent
# transform looks like. Build fails on it.
#
# The count alone is not the test, though it was until the resource set widened. Berries
# grow in genuine thickets - the largest is 61 bushes at 61 distinct coordinates, median
# 7.8 map units from the centre - and a bare count of 50 rejected two real berry patches
# while claiming they were "coordinates collapsing to a point". They were not. The
# signature of collapse is density with no spread, so both now have to hold.
MAX_PLAUSIBLE_DEPOSITS_PER_CLUSTER = 50
MIN_REAL_SPREAD_MAP_UNITS = 2.0

# --- derived difficulty (Docs/03-data-ingestion.md section 5) ----------------------
#
# These are opinions expressed as data, so the rule is versioned and published with the
# dataset: an answer has to be traceable to the rule that produced it.
DIFFICULTY_RULE = "local-wild-pal-level-v1"

# How far around a node counts as "local". Spawn areas are clustered at 25 map units, so
# 50 is the Pals you meet walking in, not the ones over the next ridge.
LOCAL_RADIUS = 50.0

# Danger bands from the local wild level. Three buckets, deliberately - the underlying
# data does not support more precision, and the boundaries are where the game's own
# progression gates sit (the first tower is level ~15, the last around 50).
DANGER_BANDS = ((20, "low"), (40, "moderate"))

# Alphas are excluded from the local level. A level 55 field boss standing beside a
# starter-zone node would push its min_player_level to 44 and hide a place low-level
# players actually farm - the boss is a thing you walk around, not the ambient danger the
# rule is trying to describe.
AMBIENT_KINDS = {"normal"}

# 03-data-ingestion.md section 5 says `max_local_wild_pal_level`, and the literal maximum
# does not survive contact with the data. In the level 1-7 starting area, a Mammorest at
# level 33-35 appears on a 1% roll; taking the max makes the beginner zone a level 35
# region, and 65% of every node on the map came out "high" danger with a median gating
# level of 44. One rare spawn poisons a whole region.
#
# The weighted 90th percentile answers the question the rule was actually asking - how
# tough are the Pals you will actually run into - by weighting each area by its expected
# encounter rate (spawn points times the share of rolls that produce that species).
# Checked against four zones of known difficulty:
#
#   zone            max   p90
#   starter          35     7
#   desert alpha     53    42
#   volcano          56    56
#   Feybreak         72    68
#
# p90 rather than the median because the danger of an area is set by its hardest common
# encounter, not its typical one - the desert reads 37 at p50 and 42 at p90, and 42 is
# what is actually going to kill someone.
LEVEL_PERCENTILE = 0.90


def local_wild_level(spawns: list[dict], radius: float):
    """Return a lookup from (x, y) to the ambient wild Pal level nearby.

    Bucketed on a grid the size of the search radius so this stays linear-ish: the naive
    version is 19k areas times 10k clusters.
    """
    buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for a in spawns:
        if a["kind"] not in AMBIENT_KINDS:
            continue
        buckets[(int(a["map_x"] // radius), int(a["map_y"] // radius))].append(a)

    def lookup(x: float, y: float) -> int | None:
        bx, by = int(x // radius), int(y // radius)
        rows = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for a in buckets.get((bx + dx, by + dy), ()):
                    if math.dist((x, y), (a["map_x"], a["map_y"])) > radius:
                        continue
                    rows.append((a["level_max"],
                                 a["spawn_points"] * a["encounter_share"]))
        total = sum(w for _, w in rows)
        if not rows or total <= 0:
            return None
        acc = 0.0
        for level, weight in sorted(rows):
            acc += weight
            if acc >= LEVEL_PERCENTILE * total:
                return level
        return max(level for level, _ in rows)

    return lookup


def danger_of(level: int | None) -> str | None:
    if level is None:
        return None
    for ceiling, label in DANGER_BANDS:
        if level <= ceiling:
            return label
    return "high"


def min_player_level_of(level: int | None, danger: str | None) -> int | None:
    """The rule from 03-data-ingestion.md section 5.

    The "+5 inside raid-triggering territory" term is NOT applied: raid territory is not
    in any table extracted so far, and inventing a proxy for it would make the rule
    untraceable to its inputs. Recorded as a known gap rather than approximated.
    """
    if level is None:
        return None
    out = math.ceil(level * 0.8) + (5 if danger == "high" else 0)
    return max(1, min(60, out))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    ap.add_argument("--radius", type=float, default=CLUSTER_RADIUS)
    args = ap.parse_args()

    class_to_res, display = derive()

    placements = json.loads((RAW / "placements.json").read_text(encoding="utf-8"))
    matched = [p for p in placements if p["cls"] in class_to_res]
    nodes = [p for p in matched if is_overworld(p)]
    instanced = len(matched) - len(nodes)
    print(f"placements: {len(placements):,}  -> resource nodes: {len(nodes):,} "
          f"across {len(display)} resources")
    if instanced:
        # Reported, not silently dropped. A sixth of the dataset disappearing should be
        # a number someone can check against the next extraction, not a quiet diff.
        print(f"  excluded {instanced:,} deposits in non-overworld cells "
              f"({instanced / len(matched):.1%}) - dungeon-local, see is_overworld")
    if not nodes:
        raise SystemExit(
            "ABORT: every deposit was excluded as non-overworld. The cell naming "
            "changed upstream - check is_overworld against data/raw/placements.json "
            "before publishing an empty dataset.")

    by_res: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_res[class_to_res[n["cls"]]].append(n)

    spawn_path = REPO / "data" / args.version / "pal_spawns.json"
    if not spawn_path.exists():
        raise SystemExit(
            f"ABORT: {spawn_path} is missing. Difficulty is derived from wild Pal levels; "
            "run build_pal_spawns.py first.")
    spawns = json.loads(spawn_path.read_text(encoding="utf-8"))["areas"]
    nearby_level = local_wild_level(spawns, LOCAL_RADIUS)

    records = []
    print(f"\n{'resource':<10}{'deposits':>10}{'clusters':>10}{'largest':>9}")
    for res, pts in sorted(by_res.items()):
        groups = cluster(pts, args.radius)
        groups.sort(key=len, reverse=True)
        print(f"{res:<10}{len(pts):>10,}{len(groups):>10,}{len(groups[0]):>9}")
        for gi, g in enumerate(groups):
            at = anchor(g)
            hints = {CLASS_TO_AREA_HINT[p["cls"]] for p in g if p["cls"] in CLASS_TO_AREA_HINT}
            wild = nearby_level(at["map_x"], at["map_y"])
            danger = danger_of(wild)
            records.append({
                "node_id": f"{res}_{gi:04d}",
                "resource": res,
                "map_x": at["map_x"],
                "map_y": at["map_y"],
                "node_count": len(g),
                "spread_map_units": round(spread(g, at["map_x"], at["map_y"]), 1),
                "area_hint": sorted(hints)[0] if hints else None,
                "world": {"x": at["world_x"], "y": at["world_y"], "z": at["world_z"]},
                "transform_id": "palworld-1.0.2-linear-axisswap-v2",
                # Derived, not upstream. The rule is published with the dataset so an
                # answer stays traceable to the rule that produced it; `local_wild_level`
                # is the input it was computed from, and is the only part of this that is
                # a fact rather than an opinion.
                "local_wild_level": wild,
                "min_player_level": min_player_level_of(wild, danger),
                "danger": danger,
            })

    out = {
        "game_version": args.version,
        "source": "BP_PalMapObjectSpawner_* and BP_LevelObject_* item-provider actors in "
                  "PL_MainWorld5 World Partition cells",
        "scope": "overworld only - dungeon and instanced maps are NOT included",
        "cluster_radius_map_units": args.radius,
        "transform_id": "palworld-1.0.2-linear-axisswap-v2",
        "difficulty_rule": DIFFICULTY_RULE,
        "difficulty_inputs": {
            "local_radius_map_units": LOCAL_RADIUS,
            "ambient_kinds": sorted(AMBIENT_KINDS),
            "local_wild_level": f"level_max at the {LEVEL_PERCENTILE:.0%} percentile of "
                                "nearby spawn areas, weighted by expected encounter rate",
            "formula": "ceil(local_wild_level * 0.8) + 5 if danger == high, clamped 1-60",
            "danger_bands": {"low": "<=20", "moderate": "21-40", "high": ">40"},
        },
        "known_gaps": [
            "min_player_level omits the '+5 inside raid-triggering territory' term - "
            "raid territory is not in any extracted table, and a proxy would make the "
            "rule untraceable to its inputs",
            "the difficulty rule is UNCALIBRATED: 03-data-ingestion.md section 5 asks for "
            "~20 nodes of known difficulty read in-game, and that has not been done",
            "SkyIslandOre and WorldTreeOre shipped as `ore` through Phase 1; they are "
            "Soralite and Paloxite and are now separate resources",
        ],
        "resource_display_names": dict(sorted(display.items())),
        # Resources you build a structure on rather than swing a pickaxe at. A coordinate
        # alone reads as "come here and mine it", and for crude oil the game's own answer
        # is "install a Crude Oil Extractor in an oil field" - so the card has to say
        # which kind of place it is pointing at. Derived, not listed: see
        # `_resources.provided`.
        "provided_resources": sorted(provided()),
        "stats": {
            "deposits": len(nodes),
            "clusters": len(records),
            "by_resource": {r: len(v) for r, v in sorted(by_res.items())},
            "with_difficulty": sum(1 for r in records
                                   if r["min_player_level"] is not None),
        },
        "nodes": sorted(records, key=lambda r: (r["resource"], -r["node_count"])),
    }

    # Fail closed: a silently wrong dataset produces confidently wrong cards.
    overdense = [r for r in records
                 if r["node_count"] > MAX_PLAUSIBLE_DEPOSITS_PER_CLUSTER
                 and r["spread_map_units"] < MIN_REAL_SPREAD_MAP_UNITS]
    if overdense:
        for r in overdense[:5]:
            print(f"  IMPLAUSIBLE: {r['node_id']} has {r['node_count']} deposits "
                  f"within {r['spread_map_units']} map units at "
                  f"({r['map_x']}, {r['map_y']})")
        raise SystemExit(
            f"\nABORT: {len(overdense)} cluster(s) exceed "
            f"{MAX_PLAUSIBLE_DEPOSITS_PER_CLUSTER} deposits. This indicates coordinates "
            "collapsing to a point, not real terrain. Not publishing.")

    dest = REPO / "data" / args.version / "resource_nodes.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\n-> {dest}")

    print(f"\ntop coal clusters (map coords):")
    for r in [r for r in out["nodes"] if r["resource"] == "coal"][:8]:
        print(f"   {r['node_count']:>3} deposits at ({r['map_x']:>7.0f}, {r['map_y']:>7.0f})")


if __name__ == "__main__":
    main()
