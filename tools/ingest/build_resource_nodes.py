"""Build the resource node dataset from extracted World Partition placements.

Input : data/raw/placements.json   (BP_PalMapObjectSpawner_* actors, world + map coords)
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
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Spawner blueprint class -> resource type. Derived from the full class inventory of
# the overworld scan; anything unmapped is deliberately excluded rather than guessed.
CLASS_TO_RESOURCE = {
    "BP_PalMapObjectSpawner_RockCoal_C": "coal",
    "BP_PalMapObjectSpawner_RockCopper_C": "ore",
    "BP_PalMapObjectSpawner_SkyIslandOre_C": "ore",
    "BP_PalMapObjectSpawner_WorldTreeOre_C": "ore",
    "BP_PalMapObjectSpawner_Sulfur_C": "sulfur",
    "BP_PalMapObjectSpawner_RockQuartz_C": "quartz",
}

# Sub-variants worth preserving: a Sky Island ore node is only reachable late, which
# matters for level gating even though the resource is the same.
CLASS_TO_AREA_HINT = {
    "BP_PalMapObjectSpawner_SkyIslandOre_C": "sky_island",
    "BP_PalMapObjectSpawner_WorldTreeOre_C": "world_tree",
}

# Cluster radius in map units. 1 map unit is about 4.6 m, so 12 units is roughly 55 m -
# a group a player can see from its centre. Leader clustering bounds cluster DIAMETER
# at 2x this value.
CLUSTER_RADIUS = 12.0


def cluster(points: list[dict], radius: float) -> list[list[dict]]:
    """Leader clustering: every member lies within `radius` of the cluster seed.

    Single-link clustering was tried first and chains badly - deposits strung along a
    cliff face merge into one 171-member "cluster" spanning a whole region, which is
    not a place a player can go. Seeding bounds cluster diameter at 2*radius by
    construction, so a cluster is always somewhere you can stand and see the group.

    Seeds are chosen densest-first so the natural centre of a group wins, rather than
    an arbitrary edge deposit.
    """
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        buckets[(int(p["map_x"] // radius), int(p["map_y"] // radius))].append(i)

    def neighbours(i: int) -> list[int]:
        bx, by = int(points[i]["map_x"] // radius), int(points[i]["map_y"] // radius)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in buckets.get((bx + dx, by + dy), ()):
                    if math.dist((points[i]["map_x"], points[i]["map_y"]),
                                 (points[k]["map_x"], points[k]["map_y"])) <= radius:
                        out.append(k)
        return out

    density = {i: len(neighbours(i)) for i in range(len(points))}
    taken = [False] * len(points)
    clusters = []

    for i in sorted(density, key=lambda k: -density[k]):
        if taken[i]:
            continue
        group = [k for k in neighbours(i) if not taken[k]]
        for k in group:
            taken[k] = True
        clusters.append([points[k] for k in group])
    return clusters


def spread(group: list[dict], cx: float, cy: float) -> float:
    """Greatest distance from the reported coordinate to any member deposit."""
    return max(math.dist((p["map_x"], p["map_y"]), (cx, cy)) for p in group)


# --- unresolved local-coordinate guard ---------------------------------------
# Some actors inside data-layer cells carry coordinates relative to a level-instance
# parent rather than absolute world coordinates. Extraction records them verbatim, so
# they land near world origin - which maps to (-344, 271), a perfectly plausible-looking
# spot on the map. Left alone they produce a phantom 171-deposit coal "hotspot".
#
# There is no clean magnitude gap between these and genuine near-origin nodes, so this
# is a conservative stopgap, not a fix. The real fix is resolving the level-instance
# transform during extraction. Excluding is the safe direction: a missing node yields an
# honest "no results", whereas a phantom node yields confidently wrong coordinates.
ORIGIN_EXCLUSION_WORLD = 2000.0

# Where world origin (0,0) lands in map space, per the validated transform. Derived
# rather than hardcoded so it follows any future transform revision.
_SCALE, _OFFSET_X, _OFFSET_Y = 458.7383, -124238.1, 157818.3
ORIGIN_MAP_POS = ((0.0 - _OFFSET_Y) / _SCALE, (0.0 - _OFFSET_X) / _SCALE)

# A cluster spans at most 2*CLUSTER_RADIUS (~110 m). More than this many deposits in that
# area is not real terrain - it means coordinates collapsed to a point. Build fails.
MAX_PLAUSIBLE_DEPOSITS_PER_CLUSTER = 50


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    ap.add_argument("--radius", type=float, default=CLUSTER_RADIUS)
    args = ap.parse_args()

    placements = json.loads((RAW / "placements.json").read_text(encoding="utf-8"))
    nodes = [p for p in placements if p["cls"] in CLASS_TO_RESOURCE]
    print(f"placements: {len(placements):,}  -> resource nodes: {len(nodes):,}")

    unresolved = [p for p in nodes
                  if max(abs(p["world_x"]), abs(p["world_y"])) < ORIGIN_EXCLUSION_WORLD]
    if unresolved:
        nodes = [p for p in nodes if p not in unresolved]
        print(f"  excluded {len(unresolved):,} with unresolved local coordinates "
              f"(|world xy| < {ORIGIN_EXCLUSION_WORLD:,.0f})")

    by_res: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_res[CLASS_TO_RESOURCE[n["cls"]]].append(n)

    records = []
    print(f"\n{'resource':<10}{'deposits':>10}{'clusters':>10}{'largest':>9}")
    for res, pts in sorted(by_res.items()):
        groups = cluster(pts, args.radius)
        groups.sort(key=len, reverse=True)
        print(f"{res:<10}{len(pts):>10,}{len(groups):>10,}{len(groups[0]):>9}")
        for gi, g in enumerate(groups):
            cx = sum(p["map_x"] for p in g) / len(g)
            cy = sum(p["map_y"] for p in g) / len(g)
            # Report an ACTUAL deposit, not the centroid. A centroid can land in a lake
            # or off a cliff; every coordinate we hand a player must be a real node.
            anchor = min(g, key=lambda p: math.dist((p["map_x"], p["map_y"]), (cx, cy)))
            hints = {CLASS_TO_AREA_HINT[p["cls"]] for p in g if p["cls"] in CLASS_TO_AREA_HINT}
            records.append({
                "node_id": f"{res}_{gi:04d}",
                "resource": res,
                "map_x": anchor["map_x"],
                "map_y": anchor["map_y"],
                "node_count": len(g),
                "spread_map_units": round(spread(g, anchor["map_x"], anchor["map_y"]), 1),
                "area_hint": sorted(hints)[0] if hints else None,
                "world": {"x": anchor["world_x"], "y": anchor["world_y"], "z": anchor["world_z"]},
                "transform_id": "palworld-1.0.2-linear-axisswap-v2",
                # Derived gating fields are NOT populated here. They require wild Pal
                # level data per area, which comes from the Pal spawner sheets.
                "min_player_level": None,
                "danger": None,
                # Clusters sitting on the map position of world origin are likely
                # residual unresolved local coordinates rather than real terrain. The
                # 2000-unit exclusion above is conservative and does not catch all of
                # them. Flagged rather than dropped, so genuine nodes there are not
                # silently lost - Q1 should exclude suspect clusters until the
                # level-instance transform is resolved properly.
                "suspect_origin_artifact": math.dist(
                    (anchor["map_x"], anchor["map_y"]), ORIGIN_MAP_POS) <= 20.0,
            })

    out = {
        "game_version": args.version,
        "source": "BP_PalMapObjectSpawner_* actors in PL_MainWorld5 World Partition cells",
        "scope": "overworld only - dungeon and instanced maps are NOT included",
        "cluster_radius_map_units": args.radius,
        "transform_id": "palworld-1.0.2-linear-axisswap-v2",
        "known_gaps": [
            "crude_oil has no spawner class in the overworld; it is not a placed node",
            "min_player_level and danger are unpopulated - they need wild Pal level data",
            f"{len(unresolved)} deposits excluded: coordinates are relative to a "
            "level-instance parent and were not resolved to world space. Proper fix is "
            "resolving that transform during extraction; excluding is the safe stopgap.",
        ],
        "stats": {
            "deposits": len(nodes),
            "clusters": len(records),
            "by_resource": {r: len(v) for r, v in sorted(by_res.items())},
        },
        "nodes": sorted(records, key=lambda r: (r["resource"], -r["node_count"])),
    }

    # Fail closed: a silently wrong dataset produces confidently wrong cards.
    overdense = [r for r in records
                 if r["node_count"] > MAX_PLAUSIBLE_DEPOSITS_PER_CLUSTER]
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
