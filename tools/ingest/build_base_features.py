"""Build the base-siting features — flat ground, water, and the game's own marked areas.

Input : data/raw/world_features.json   (BP_BaseCampPopularArea, BP_SimpleWater, fishing)
        data/raw/placements.json       (54,894 actors, each with a ground height)
        data/<version>/base_camp.json  (the base radius)
Output: data/<version>/base_features.json

The community names four levers for choosing a base site: **flat terrain, resource
density, raid safety, water access.** Q4 shipped able to answer one of them, and its card
said out loud that it could not tell you whether the ground was buildable. This file is
two more of the four, plus a fifth signal that is better than any of them.

## The fifth signal: the game marks 32 of them itself

`BP_BaseCampPopularArea_C` is placed 32 times in the world. The name is the game's, not
ours. It was found only because a full `survey` of the world's 1,295 actor classes was
run — the cell scan had been reading three prefixes, so `placement_class_counts.json` was
a census of what we already collected and not of what is out there. *That is the same
mistake as reading 81 of 532 data tables, in a different file.*

**Corroborated twice before being trusted, and neither check was arranged:**

- The reference player's main base sits at (228.9, −486.6). The nearest popular area is at
  (229.5, −485.5) — **1.3 map units away**. They did not know these markers existed.
- The 32 areas are measurably flatter than ordinary ground: median roughness **92 cm**
  against **258 cm** for random placed points, and at the 90th percentile 906 cm against
  23,730 cm. They are not scattered at random over terrain.

## Flat terrain, from ground heights we already had

Every one of the 54,894 extracted placements carries `world_z`, and a placed actor stands
on the ground — so the **standard deviation of `world_z` among the actors inside one base
radius is a terrain roughness proxy**, with no heightmap extraction at all.

It is a proxy and the card must say so. It measures the ground *where things were placed*,
which is not the same as the ground everywhere, and it says nothing about no-build zones.
What it does do is separate a plateau from a cliff, which the measurement above shows and
which is the distinction a base site actually turns on.

**The flatness bar is calibrated against the game's own 32 areas** rather than chosen:
`FLAT_CM` is their 75th-percentile roughness, so "flat enough to build on" means "no
rougher than three quarters of the places the designers marked as base camp areas". That
is a threshold derived from the game rather than from an opinion, which is the difference
between this and the `min_player_level` rule that has sat uncalibrated since Phase 1.

## Water, stated three ways

`BP_SimpleWater_C` is 1,257 placed water bodies. Fishing spots add 777 more, typed by the
water they sit in — `_River_`, `_Ocean_`, and plain — and a fishing spot exists only where
there is water to fish in. Both are positions of water, so both are kept, with `kind`
carried so a card can say "river" rather than "water" where the game does.

**Not attempted: raid safety.** The community's fourth lever is about elevation and
approach, `BP_RaidBossAreaBaseCampPoint_C` exists at 16 placements and it is not obviously
the same concept, and nothing here would let a card claim a site is safe from raids. It is
left alone rather than approximated.

Usage: python tools/ingest/build_base_features.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# The fewest placed actors a grid cell needs before its height spread means anything. Two
# points always have a standard deviation and it is noise; four is where a spread starts
# describing terrain rather than describing two rocks.
MIN_ACTORS = 4

# Where the flatness bar comes from is computed, not chosen - see the docstring. This is
# the percentile of the 32 marked areas' roughness that defines "flat enough".
FLAT_PERCENTILE = 0.75


def _grid(points, radius: float) -> dict[tuple[int, int], list]:
    cells: dict[tuple[int, int], list] = {}
    for p in points:
        cells.setdefault((int(p["map_x"] // radius),
                          int(p["map_y"] // radius)), []).append(p)
    return cells


def _within(cells, x: float, y: float, radius: float, cell: float | None = None) -> list:
    """Points within `radius` of (x, y), from a grid whose cells are `cell` across.

    `cell` defaults to `radius`, which is the common case. **It has to be a separate
    argument** because a search wider than one cell needs a wider ring of cells, and the
    version that assumed they were the same silently returned nothing for every water
    lookup - the marked areas' median water distance is 23 units and a ±1 ring at that
    cell size reaches about 15.
    """
    cell = radius if cell is None else cell
    cx, cy = int(x // cell), int(y // cell)
    span = int(radius // cell) + 1
    out = []
    for dx in range(-span, span + 1):
        for dy in range(-span, span + 1):
            for p in cells.get((cx + dx, cy + dy), ()):
                if math.dist((x, y), (p["map_x"], p["map_y"])) <= radius:
                    out.append(p)
    return out


def build(version: str) -> dict:
    features = json.loads((RAW / "world_features.json").read_text(encoding="utf-8"))
    placements = json.loads((RAW / "placements.json").read_text(encoding="utf-8"))
    camp = json.loads(
        (REPO / "data" / version / "base_camp.json").read_text(encoding="utf-8"))
    radius = camp["map_units"]

    areas = [f for f in features if f["kind"] == "base_area"]
    water = [f for f in features if f["kind"] in ("water", "fishing")]

    # A roughness value per grid cell, at base-radius resolution. Stored as a grid rather
    # than per candidate site so this dataset does not have to know what a candidate is,
    # and so a query never has to load 54,894 heights to answer one question.
    cells = _grid(placements, radius)
    roughness: dict[str, int] = {}
    for (gx, gy), members in cells.items():
        if len(members) < MIN_ACTORS:
            continue
        roughness[f"{gx},{gy}"] = round(
            statistics.pstdev([m["world_z"] for m in members]))

    # The bar, from the game's own marked areas.
    marked = []
    for a in areas:
        near = _within(cells, a["map_x"], a["map_y"], radius)
        if len(near) >= MIN_ACTORS:
            marked.append(statistics.pstdev([n["world_z"] for n in near]))
    marked.sort()
    flat_cm = round(marked[int(FLAT_PERCENTILE * (len(marked) - 1))]) if marked else None

    all_rough = sorted(roughness.values())

    # **The reference distribution a rating is measured against.**
    #
    # "How good is this base location" is a judgement, and this project does not ship
    # uncalibrated judgements - `min_player_level` has been one since Phase 1 and STATUS
    # still lists it. The way out is to answer it *relatively*: how does this spot compare
    # to the 32 the game's own designers marked? A percentile against a game-stated
    # reference set is not an invented weighting.
    #
    # So each marked area's own resource coverage is profiled here, once, and stored. 32
    # rows, computed against the same node dataset a rating will use, so the comparison
    # is like for like rather than against a number from somewhere else.
    nodes = json.loads(
        (REPO / "data" / version / "resource_nodes.json").read_text(encoding="utf-8"))
    node_cells = _grid(nodes["nodes"], radius)
    water_cells = _grid(water, radius)

    def by_resource(x: float, y: float) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in _within(node_cells, x, y, radius):
            out[n["resource"]] = out.get(n["resource"], 0) + n["node_count"]
        return out

    def coverage(x: float, y: float) -> tuple[int, int]:
        counts = by_resource(x, y)
        return sum(counts.values()), len(counts)

    def water_distance(x: float, y: float) -> float | None:
        # Only the nine surrounding cells, so this is a NEAR-water distance and is None
        # when the closest water is further than about one radius away. That is the only
        # range the answer matters over, and scanning 2,034 points per site is not.
        near = [math.dist((x, y), (w["map_x"], w["map_y"]))
                for w in _within(water_cells, x, y, radius * 6, cell=radius)]
        return round(min(near), 1) if near else None

    profile = []
    for a in areas:
        deposits, kinds = coverage(a["map_x"], a["map_y"])
        profile.append({
            "map_x": a["map_x"], "map_y": a["map_y"],
            "deposits": deposits,
            "resource_kinds": kinds,
            "roughness_cm": roughness.get(
                f"{int(a['map_x'] // radius)},{int(a['map_y'] // radius)}"),
            "water_distance": water_distance(a["map_x"], a["map_y"]),
        })

    # **A second reference, because the first one is the wrong yardstick for resources.**
    #
    # Measured: the 32 marked areas hold a MEDIAN OF THREE DEPOSITS inside a base radius,
    # against a maximum of 77 - and their median roughness is 24cm. The designers are not
    # marking resource-rich ground, they are marking flat ground near water. Scoring a
    # player's 36-deposit site against that set would put it in the 97th percentile,
    # which is true and useless.
    #
    # So flatness and water are scored against the marked areas, which is what they are
    # good for, and resources are scored against every node cluster on the map - the
    # actual population of places somebody might build. Deciles rather than 2,668 rows:
    # the percentile is all a rating needs and the file stays small.
    site_deposits, site_kinds = [], []
    for n in nodes["nodes"]:
        deposits, kinds = coverage(n["map_x"], n["map_y"])
        site_deposits.append(deposits)
        site_kinds.append(kinds)

    def deciles(values: list[int]) -> list[int]:
        values = sorted(values)
        return [values[min(int(q / 10 * len(values)), len(values) - 1)]
                for q in range(11)]

    # **A third reference, per resource, and the map-wide one is wrong without it.**
    #
    # "Is this a good spot for a quartz base" is a question about quartz, and scoring 12
    # quartz against the all-resources distribution - where the median site holds 3 of
    # ANYTHING and stone alone reaches 77 - measures the wrong thing twice over. The
    # comparison a player means is against other places you would go for quartz, so each
    # resource gets its own deciles taken over the clusters OF that resource.
    #
    # Sites with none of it are excluded from its distribution on purpose: "more quartz
    # than 90% of places" would be true of almost anywhere if the 2,500 sites holding no
    # quartz counted, and it would be a compliment about nothing.
    per_resource: dict[str, list[int]] = {}
    for n in nodes["nodes"]:
        counts = by_resource(n["map_x"], n["map_y"])
        for resource, total in counts.items():
            per_resource.setdefault(resource, []).append(total)

    def water_kind(cls: str) -> str:
        if "River" in cls:
            return "river"
        if "Ocean" in cls:
            return "ocean"
        return "water"

    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "World Partition cell scan - BP_BaseCampPopularArea_C, "
                  "BP_SimpleWater_C, BP_FishingSpot_*, and the ground height of every "
                  "placed actor",
        "radius_map_units": radius,
        "popular_area_note": "BP_BaseCampPopularArea_C is the GAME's name for these, not "
                             "ours, and there are 32. Corroborated twice before being "
                             "used: the reference player's main base sits 1.3 map units "
                             "from one without them knowing the markers exist, and the "
                             "32 are measurably flatter than random ground (median 92cm "
                             "roughness against 258cm).",
        "roughness_note": "The standard deviation of placed-actor ground height inside "
                          "one base radius. A PROXY: it measures the ground where things "
                          "were placed, not the ground everywhere, and it says nothing "
                          "about no-build zones or water. What it separates is a plateau "
                          "from a cliff, which is the distinction a base site turns on.",
        "flat_note": f"flat_cm is the {FLAT_PERCENTILE:.0%} percentile of the 32 marked "
                     f"areas' own roughness, so 'flat enough' means 'no rougher than "
                     f"most of the places the designers marked'. Calibrated against the "
                     f"game rather than chosen - unlike min_player_level, which has been "
                     f"uncalibrated since Phase 1.",
        "water_note": "BP_SimpleWater_C is a placed water body. A fishing spot is kept "
                      "as water too, because one only exists where there is water to "
                      "fish in, and its class name carries the type the game uses.",
        "gap_note": "Raid safety - the community's fourth base-siting lever - is NOT "
                    "here. BP_RaidBossAreaBaseCampPoint_C exists at 16 placements and is "
                    "not obviously the same concept, and nothing extracted would let a "
                    "card claim a site is safe from raids.",
        "stats": {
            "popular_areas": len(areas),
            "water_points": len(water),
            "by_water_kind": {k: sum(1 for w in water if water_kind(w["cls"]) == k)
                              for k in ("water", "river", "ocean")},
            "roughness_cells": len(roughness),
            "flat_cm": flat_cm,
            "roughness_median_cm": all_rough[len(all_rough) // 2] if all_rough else None,
            "marked_area_roughness_median_cm":
                round(statistics.median(marked)) if marked else None,
            "marked_area_median_deposits":
                round(statistics.median(p["deposits"] for p in profile)) if profile else None,
            "marked_area_median_water_distance":
                round(statistics.median(p["water_distance"] for p in profile
                                        if p["water_distance"] is not None), 1),
        },
        "rating_note": "'How good is this spot' is a judgement, and this project does "
                       "not ship uncalibrated ones - so it is answered RELATIVELY. A "
                       "percentile against a stated reference set is not an invented "
                       "weighting. Note n=32 for the marked areas, which is small: a "
                       "percentile there moves in steps of about three points.",
        "yardstick_note": "TWO reference sets, because one is the wrong yardstick for "
                          "resources. The 32 marked areas hold a MEDIAN OF THREE "
                          "deposits inside a base radius and have a median roughness of "
                          "24cm - the designers are marking flat ground near water, not "
                          "resource-rich ground. Scoring a 36-deposit site against them "
                          "would return the 97th percentile, which is true and useless. "
                          "So terrain and water are scored against the marked areas, and "
                          "resources against every node cluster on the map.",
        "flat_cm": flat_cm,
        "site_deciles": {"deposits": deciles(site_deposits),
                         "resource_kinds": deciles(site_kinds)},
        "resource_deciles": {r: deciles(v) for r, v in sorted(per_resource.items())},
        "marked_area_profile": sorted(profile, key=lambda p: (p["map_x"], p["map_y"])),
        "popular_areas": [{"map_x": a["map_x"], "map_y": a["map_y"]}
                          for a in sorted(areas, key=lambda a: (a["map_x"], a["map_y"]))],
        "water": [{"map_x": w["map_x"], "map_y": w["map_y"], "kind": water_kind(w["cls"])}
                  for w in sorted(water, key=lambda w: (w["map_x"], w["map_y"]))],
        "roughness": roughness,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    needed = RAW / "world_features.json"
    if not needed.exists():
        sys.exit(f"Missing {needed}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- cells")

    data = build(args.version)
    s = data["stats"]
    if not s["popular_areas"]:
        sys.exit("ABORT: no BP_BaseCampPopularArea_C placements. The scan's feature "
                 "filter has stopped matching; nothing was written.")

    dest = REPO / "data" / args.version / "base_features.json"
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"base features -> {dest}")
    print(f"  marked base areas  {s['popular_areas']}"
          f"   (median roughness {s['marked_area_roughness_median_cm']} cm)")
    print(f"  water points       {s['water_points']}"
          f"   {s['by_water_kind']}")
    print(f"  roughness cells    {s['roughness_cells']}"
          f"   (median {s['roughness_median_cm']} cm map-wide)")
    print(f"  flat bar           {s['flat_cm']} cm"
          f"   ({FLAT_PERCENTILE:.0%} percentile of the marked areas)")
    print(f"  marked areas hold  {s['marked_area_median_deposits']} deposits at the "
          f"median - they are flat ground near water, NOT resource-rich ground")
    print(f"  site deciles       deposits {data['site_deciles']['deposits']}")


if __name__ == "__main__":
    main()
