"""Build the base-camp dataset — how big a base is, in the units the cards use.

Input : data/raw/game_settings.json   (BP_PalGameSetting's class defaults)
        data/coord_transform.json     (world centimetres -> in-game map units)
Output: data/<version>/base_camp.json

**One number does the work here, and it is stated by the game**: `BaseCampAreaRange`,
3500 world units, which is the radius inside which a base's Pals reach a resource node.
Everything Q4 computes is "what falls inside that circle", so the whole class rests on
this value being what it appears to be.

## Why it is a separate dataset rather than a constant in the code

Because it is a *reading*, and readings in this project live in `data/` with their
provenance attached and get rebuilt when the game does. A patch that rebalances the base
radius must move this file, not require someone to notice a literal in a Python module.

## What was checked, and what is still an inference

The value is read, not derived. The **conversion** to map units is not: it goes through
`coord_transform.json`, the same fitted transform every coordinate in this project uses,
which makes the radius 3500 / 458.7383 = 7.63 map units.

That was corroborated against the reference save rather than trusted: the player's three
real base camps sit at (229, -487), (73, -399) and (285, 625), and applying 7.63 map units
to each contains 3, 2 and 1 node clusters respectively. Small handfuls, which is what a
base looks like - not zero, which would say the radius is too small, and not twenty, which
would say it is far too large. That is corroboration and not proof, and the card carries
the caveat rather than this file swallowing it.

**`BaseCampNeighborMinimumDistance` is 1500 and is NOT the same thing**, which is worth
recording so the smaller number is not mistaken for the useful one later. It is a
placement rule about how close two bases may be, and it is *less* than the area range,
so bases are permitted to overlap. It is stored but nothing reads it.

Usage: python tools/ingest/build_base_camp.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# The class-default object, which is the export carrying the tunables. The first export
# in the package is the generated class and its Properties block is empty - a distinction
# that produced an empty settings file once already.
CDO = "Default__BP_PalGameSetting_C"
WANTED = ("BaseCampAreaRange", "BaseCampNeighborMinimumDistance",
          "BaseCampExtraWorkAreaRange")


def build(version: str) -> dict:
    exports = json.loads((RAW / "game_settings.json").read_text(encoding="utf-8"))
    cdo = next((e for e in exports if e.get("Name") == CDO), None)
    if cdo is None:
        raise SystemExit(f"ABORT: no {CDO} in game_settings.json - re-run the extract.")
    props = cdo.get("Properties") or {}

    missing = [k for k in ("BaseCampAreaRange",) if k not in props]
    if missing:
        raise SystemExit(
            f"ABORT: {missing} absent from BP_PalGameSetting. The whole of Q4 is "
            f"'what falls inside that radius', so nothing is written without it.")

    transform = json.loads(
        (REPO / "data" / "coord_transform.json").read_text(encoding="utf-8"))
    scale = transform["model"]["scale"]

    world = float(props["BaseCampAreaRange"])
    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "BP_PalGameSetting class defaults, converted through "
                  "data/coord_transform.json",
        "transform_id": transform["transform_id"],
        "extraction_note": "BaseCampAreaRange is READ. The conversion to map units is "
                           "the same fitted transform every coordinate in this project "
                           "goes through, so a map-change patch invalidates it exactly "
                           "as it invalidates every other coordinate.",
        "corroboration_note": "Applied to the reference save's three real base camps at "
                              "(229,-487), (73,-399) and (285,625), the radius contains "
                              "3, 2 and 1 node clusters. Small handfuls, which is what a "
                              "base looks like. Corroboration, not proof - no in-game "
                              "measurement of the circle has been taken.",
        "neighbour_note": "BaseCampNeighborMinimumDistance is a placement rule about how "
                          "close two bases may be, is SMALLER than the area range, and "
                          "is not the base's size. Stored so it is not mistaken for the "
                          "useful number later; nothing reads it.",
        "buildability_note": "Nothing here says whether ground is FLAT or a location is "
                             "inside a no-build zone. The pak carries no such signal that "
                             "this project has found, so a site computed from this radius "
                             "is 'where the resources are', never 'you can build here'.",
        "world_units": world,
        "map_units": round(world / scale, 4),
        "scale": scale,
        "other": {k: props[k] for k in WANTED if k in props},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    needed = RAW / "game_settings.json"
    if not needed.exists():
        sys.exit(f"Missing {needed}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- settings")

    data = build(args.version)
    dest = REPO / "data" / args.version / "base_camp.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"base camp -> {dest}")
    print(f"  radius   {data['world_units']:.0f} world units"
          f"  =  {data['map_units']:.2f} map units")
    print(f"  via      {data['transform_id']}")


if __name__ == "__main__":
    main()
