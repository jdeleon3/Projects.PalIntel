"""Publish card artwork: world-map tiles and one icon per Pal.

Input : data/raw/textures/     (PakExtract.exe textures)
        data/raw/placements.json
        data/<version>/lexicon.json
Output: data/<version>/assets/

Two things here are not obvious and both are places a confidently-wrong card comes from.

**There is more than one map.** DT_WorldMapUIData describes two regions, and the World
Tree sits entirely outside the main island's rectangle. Drawing a Tree coordinate on the
main map places a marker in open sea - a picture that looks authoritative and is wrong,
which is the exact failure Docs/adr/0010 organises against. Regions are published with
their bounds and a priority, and a coordinate matching none of them gets no map.

**The pixel orientation is not assumed.** The map bounds say which world rectangle the
texture covers; they do not say which way round it is drawn, and the world -> map
transform already turned out to have swapped axes (Docs/04-roadmap.md, spike 0.5). So the
orientation is measured rather than guessed: every extracted placement stands on terrain,
so the right orientation is the one that lands them on it.

Three classifiers vote, because no single one is strong on both maps and picking the one
that looked decisive on the map in front of me is how a measurement gets fitted to its
example. Each is weak somewhere - the colour rule reads the Tree's dark forest floor as
ocean, "not background" barely separates anything on a map that is mostly sea, and local
detail is the mushiest of the three:

    classifier      MainMap margin   Tree margin
    land colour          30.9%          14.5%
    not background        2.3%          13.7%
    local detail         19.0%           9.7%

All three name the same orientation on both maps, and that agreement - 3 independent
signals converging on 1 of 8 candidates - is the gate, not any one margin.

Usage: python tools/ingest/build_assets.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _resources import item_ids  # noqa: E402

# The basemaps are 8192x8192. Pillow's decompression-bomb guard trips well below that,
# and it is guarding against untrusted downloads, not a file we extracted ourselves.
Image.MAX_IMAGE_PIXELS = None

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"
TEXTURES = RAW / "textures"

# Tiles, because a card needs a small crop and the source is a 268 MB decode. At 512 a
# typical crop touches at most four of them, so the runtime reads a few hundred KB
# instead of the whole island.
TILE = 512

# The second zoom level: the whole region in one small image, for crops too wide to be
# worth assembling from tiles. Small enough to hold decoded and crop from at any spread.
OVERVIEW = 1024

# The margin the best-separating classifier must win by. The other two only have to
# agree with it. Measured margins are 30.9% (MainMap) and 14.5% (Tree), so this fires on
# a real break - a patch reorienting a texture, or a bounds row changing - not on drift.
MIN_BEST_MARGIN = 0.08

# Sampled rather than exhaustive: 3,000 of 54,863 placements settle a choice between
# eight discrete orientations to well under a percent, and the check runs in a second.
SAMPLE = 3000


def _classifiers(image: Image.Image):
    """Three independent "is this pixel terrain" tests over one basemap.

    None of them is a good terrain classifier and none needs to be. Each only has to
    rank the true orientation above seven wrong ones, and they fail in different places,
    which is the property that makes their agreement worth more than any single score.
    """
    rgb = image.load()
    grey = image.convert("L").load()
    w, h = image.size

    # Whatever fills the corners is the out-of-map background on both regions: a near
    # black with a teal glow at the map's edge.
    corners = [rgb[8, 8], rgb[w - 8, 8], rgb[8, h - 8], rgb[w - 8, h - 8]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    def land(x, y):
        # Ocean is blue-dominant and dark; terrain is warmer or brighter, which covers
        # snow, ash and the volcano's dark reds. Blind to the Tree's dark forest floor.
        r, g, b = rgb[x, y]
        return r > b or max(r, g, b) > 120

    def not_background(x, y):
        # Only asks whether the point is on the map at all. Strong on the Tree, which is
        # landmass surrounded by background; nearly useless on a map that is mostly sea.
        return sum(abs(rgb[x, y][i] - bg[i]) for i in range(3)) > 40

    def detail(x, y):
        # Terrain art carries local contrast - cliffs, trees, rock. Water and background
        # are smooth. Independent of hue, so it survives a region with unusual palette.
        r = 4
        x = min(w - 1 - r, max(r, x))
        y = min(h - 1 - r, max(r, y))
        patch = [grey[x + dx, y + dy] for dx in (-r, 0, r) for dy in (-r, 0, r)]
        return statistics.pstdev(patch) > 6

    return {"land": land, "not_background": not_background, "detail": detail}


# The eight ways a square texture can be laid over a world rectangle: which world axis
# drives the column, and whether either runs backwards.
ORIENTATIONS = [(col, flip_col, flip_row)
                for col in ("x", "y")
                for flip_col in (False, True)
                for flip_row in (False, True)]


def _project(wx: float, wy: float, bounds: dict, orient: tuple, w: int, h: int):
    col_axis, flip_col, flip_row = orient
    fx = (wx - bounds["world_min_x"]) / (bounds["world_max_x"] - bounds["world_min_x"])
    fy = (wy - bounds["world_min_y"]) / (bounds["world_max_y"] - bounds["world_min_y"])
    u, v = (fx, fy) if col_axis == "x" else (fy, fx)
    if flip_col:
        u = 1 - u
    if flip_row:
        v = 1 - v
    return u * w, v * h


def measure_orientation(image: Image.Image, bounds: dict,
                        points: list[tuple[float, float]]) -> dict:
    """Put every orientation to three votes and report what each one said."""
    w, h = image.size
    projected = {o: [(min(w - 1, max(0, int(x))), min(h - 1, max(0, int(y))))
                     for x, y in (_project(wx, wy, bounds, o, w, h) for wx, wy in points)]
                 for o in ORIENTATIONS}

    votes = {}
    for name, test in _classifiers(image).items():
        scores = sorted(((sum(test(x, y) for x, y in projected[o]) / len(points), o)
                         for o in ORIENTATIONS), reverse=True)
        votes[name] = {"winner": scores[0][1], "score": round(scores[0][0], 4),
                       "runner_up": round(scores[1][0], 4),
                       "margin": round(scores[0][0] - scores[1][0], 4)}

    winners = {v["winner"] for v in votes.values()}
    best = max(votes.values(), key=lambda v: v["margin"])
    orient = votes["land"]["winner"]
    return {
        "col_axis": orient[0], "flip_col": orient[1], "flip_row": orient[2],
        "unanimous": len(winners) == 1,
        "best_margin": best["margin"],
        "n_points": len(points),
        "votes": {k: {"score": v["score"], "runner_up": v["runner_up"],
                      "margin": v["margin"],
                      "winner": f"col<-{v['winner'][0]}"
                                f"{' flipcol' if v['winner'][1] else ''}"
                                f"{' fliprow' if v['winner'][2] else ''}"}
                  for k, v in votes.items()},
    }


def cut_tiles(image: Image.Image, dest: Path) -> dict:
    """Slice the basemap into a tile grid, plus one whole-region overview.

    Two zoom levels, because one is not enough at either end. Tiles serve the common
    case - a crop a few hundred metres across - without decoding a 268 MB basemap. The
    overview serves the case that made them insufficient: a Pal whose spawn areas are
    1,000 map units apart needs a crop spanning 3,570 px, which composites 64 tiles and
    resizes 12.7 megapixels, and that measured 472 ms against 19 ms for a tight cluster.
    Cropping the overview instead is bounded work at any spread.

    JPEG, not PNG: both basemaps are fully opaque, the art is photographic, and the
    difference is 100 MB against 10. Markers are drawn after decode, so nothing the
    player reads a coordinate off ever passes through the compressor.
    """
    dest.mkdir(parents=True, exist_ok=True)
    w, h = image.size
    cols, rows = -(-w // TILE), -(-h // TILE)
    for r in range(rows):
        for c in range(cols):
            box = (c * TILE, r * TILE, min((c + 1) * TILE, w), min((r + 1) * TILE, h))
            image.crop(box).convert("RGB").save(dest / f"{c}_{r}.jpg", quality=90,
                                                optimize=True)
    overview = image.resize((OVERVIEW, OVERVIEW), Image.LANCZOS)
    overview.convert("RGB").save(dest / "overview.jpg", quality=88, optimize=True)
    return {"tile_size": TILE, "tile_cols": cols, "tile_rows": rows,
            "image_w": w, "image_h": h, "overview_px": OVERVIEW}


def build(version: str) -> dict:
    manifest = json.loads((TEXTURES / "manifest.json").read_text(encoding="utf-8"))
    transform = json.loads(
        (REPO / "data" / "coord_transform.json").read_text(encoding="utf-8"))["model"]
    scale, off_x, off_y = transform["scale"], transform["offset_x"], transform["offset_y"]

    placements = json.loads((RAW / "placements.json").read_text(encoding="utf-8"))
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))

    out = REPO / "data" / version / "assets"
    regions = []

    for region in manifest["map_regions"]:
        if not region.get("file"):
            print(f"  {region['region']}: no texture exported, skipped")
            continue

        image = Image.open(TEXTURES / region["file"]).convert("RGB")

        inside = [(p["world_x"], p["world_y"]) for p in placements
                  if region["world_min_x"] <= p["world_x"] <= region["world_max_x"]
                  and region["world_min_y"] <= p["world_y"] <= region["world_max_y"]]
        if not inside:
            # No placement falls inside it, so nothing can validate the orientation and
            # nothing will ever be drawn on it either. Publishing it unvalidated would
            # mean the first coordinate that does land here is drawn on a guess.
            print(f"  {region['region']}: no placements inside its bounds, skipped")
            continue

        random.seed(0)
        sample = random.sample(inside, min(SAMPLE, len(inside)))
        orient = measure_orientation(image, region, sample)
        if not orient["unanimous"] or orient["best_margin"] < MIN_BEST_MARGIN:
            why = ("the three classifiers disagree" if not orient["unanimous"]
                   else f"the best margin is {orient['best_margin']:.1%}, "
                        f"under the {MIN_BEST_MARGIN:.0%} floor")
            detail = "\n".join(f"    {k:<15} {v['winner']:<22} "
                               f"{v['score']:.1%} vs {v['runner_up']:.1%}"
                               for k, v in orient["votes"].items())
            raise SystemExit(
                f"{region['region']}: orientation is not resolved - {why}.\n{detail}\n"
                f"  The texture layout or the map bounds changed. Re-derive it before "
                f"publishing, or every marker drawn on this region is a guess.")

        geometry = cut_tiles(image, out / "map" / region["region"].lower())

        # Published in map units, because that is what a ResourceNode carries and what
        # the player reads off the in-game map. Note the axis swap comes from the
        # transform and the row inversion from the measurement above: map_y runs UP the
        # image, so top and bottom are named rather than min and max.
        col_from_y = orient["col_axis"] == "y"
        wx0, wx1 = region["world_min_x"], region["world_max_x"]
        wy0, wy1 = region["world_min_y"], region["world_max_y"]
        if not col_from_y:
            raise SystemExit(
                f"{region['region']}: column measured as world X, but the map transform "
                f"derives map_x from world Y. One of the two is wrong; refusing to "
                f"publish a mapping that cannot be expressed in map units.")

        left, right = (wy0 - off_y) / scale, (wy1 - off_y) / scale
        top, bottom = (wx1 - off_x) / scale, (wx0 - off_x) / scale
        if orient["flip_col"]:
            left, right = right, left
        if not orient["flip_row"]:
            top, bottom = bottom, top

        regions.append({
            "region": region["region"],
            "priority": region["priority"],
            "dir": f"map/{region['region'].lower()}",
            **geometry,
            "map_x_left": round(left, 3),
            "map_x_right": round(right, 3),
            "map_y_top": round(top, 3),
            "map_y_bottom": round(bottom, 3),
            "px_per_map_unit": round(geometry["image_w"] / abs(right - left), 4),
            "orientation": orient,
        })
        print(f"  {region['region']:<10} {geometry['tile_cols']}x{geometry['tile_rows']} "
              f"tiles  orientation unanimous, best margin "
              f"{orient['best_margin']:.1%}")
        for name, v in orient["votes"].items():
            print(f"      {name:<15} {v['score']:.1%} vs {v['runner_up']:.1%}")

    icons_src = TEXTURES / "icon"
    icons_out = out / "icon"
    icons_out.mkdir(parents=True, exist_ok=True)
    available = {p.stem for p in icons_src.glob("*.png")}

    icons: dict[str, str] = {}
    missing: list[str] = []
    for pal in lexicon["pals"]:
        hit = next((i for i in pal["internal_ids"] if i in available), None)
        if hit is None:
            missing.append(pal["canonical"])
            continue
        # Keyed on the canonical name the card prints, filed under the internal id the
        # asset actually uses. "Zoe & Grizzbolt" is not a filename.
        icons[pal["canonical"]] = f"icon/{hit}.png"
        dest = icons_out / f"{hit}.png"
        if not dest.exists():
            dest.write_bytes((icons_src / f"{hit}.png").read_bytes())

    deck = [p["canonical"] for p in lexicon["pals"] if p["in_paldeck"]]
    deck_missing = [c for c in missing if c in set(deck)]

    # Resource icons. The filename keeps the game's category prefix (Material_Coal,
    # Food_Berries) because item ids contain underscores themselves, so the id is matched
    # against the stem after the FIRST underscore rather than parsed out of it.
    item_src = TEXTURES / "item"
    by_item = {p.stem.split("_", 1)[1]: p for p in item_src.glob("*.png")
               if "_" in p.stem}
    resource_icons: dict[str, str] = {}
    resources_missing: list[str] = []
    for canonical, item_id in item_ids().items():
        source = by_item.get(item_id)
        if source is None:
            resources_missing.append(f"{canonical} ({item_id})")
            continue
        resource_icons[canonical] = f"item/{source.name}"
        dest = out / "item" / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            dest.write_bytes(source.read_bytes())

    return {
        "assets_version": 1,
        "game_version": version,
        "source": manifest["source"],
        "note": "Map bounds are the game's own (DT_WorldMapUIData). Pixel orientation is "
                "measured against known-terrain placements, not assumed. A coordinate "
                "outside every region has no map and must render none.",
        "stats": {
            "regions": len(regions),
            "icons": len(icons),
            "icon_files_available": len(available),
            "pals_without_icon": len(missing),
            "paldeck_without_icon": len(deck_missing),
            "resource_icons": len(resource_icons),
            "resources_without_icon": len(resources_missing),
        },
        "map_regions": sorted(regions, key=lambda r: -r["priority"]),
        "icons": dict(sorted(icons.items())),
        "resource_icons": dict(sorted(resource_icons.items())),
        "pals_without_icon": sorted(missing),
        "resources_without_icon": sorted(resources_missing),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    if not (TEXTURES / "manifest.json").exists():
        sys.exit(f"No textures at {TEXTURES}.\n"
                 f"  Run: dotnet run --project tools/extract/PakExtract -- textures")

    assets = build(args.version)
    dest = REPO / "data" / args.version / "assets" / "manifest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(assets, indent=2, ensure_ascii=False), encoding="utf-8")

    s = assets["stats"]
    print(f"\nassets -> {dest}")
    print(f"  map regions          {s['regions']}")
    print(f"  icons published      {s['icons']} of {s['icon_files_available']} extracted")
    print(f"  pals without an icon {s['pals_without_icon']}"
          f"  ({s['paldeck_without_icon']} of them in the Paldeck)")
    if assets["pals_without_icon"]:
        shown = assets["pals_without_icon"][:8]
        print(f"    {', '.join(shown)}"
              + (" ..." if len(assets["pals_without_icon"]) > len(shown) else ""))


if __name__ == "__main__":
    main()
