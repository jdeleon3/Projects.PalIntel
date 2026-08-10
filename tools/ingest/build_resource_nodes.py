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
from collections import defaultdict
from pathlib import Path

from _cluster import anchor, cluster, spread

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
            at = anchor(g)
            hints = {CLASS_TO_AREA_HINT[p["cls"]] for p in g if p["cls"] in CLASS_TO_AREA_HINT}
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
                # Derived gating fields are NOT populated here. They require wild Pal
                # level data per area, which comes from the Pal spawner sheets.
                "min_player_level": None,
                "danger": None,
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
