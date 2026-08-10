"""Map crops for location answers — deterministic drawing, no model in the path.

A picture of where a node is answers "where's the nearest coal?" better than a pair of
numbers does, but it is also a much easier place to be confidently wrong: a coordinate a
player cannot parse is obviously useless, whereas a marker on the wrong island looks
authoritative. So this module refuses more readily than it draws.

Two refusals matter and both return None rather than a best effort:

  * **A coordinate belonging to no published region.** There is more than one world map
    (Docs/03-data-ingestion.md), and the World Tree lies entirely outside the main
    island's rectangle. Drawing a Tree coordinate on the main map puts a marker in open
    sea.
  * **Points spread across two regions.** Cropping to one of them would silently drop
    the others while the card above still lists them, so the picture and the text would
    disagree about how many answers there are.

Every value drawn here comes from the same typed result the card text is interpolated
from, so this does not widen the surface ADR-0006 protects - it is the same numbers,
plotted.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - the bot runs fine without map rendering
    Image = None


# A tight cluster of nodes is a few map units across, and cropping to it would upscale a
# handful of source pixels into a blur with no landmark in it. 200 units (~920 m) keeps
# a coastline or a lake in frame, which is what makes the picture navigable.
MIN_EXTENT = 200.0
PADDING = 0.18          # of the point spread, so markers never sit against the edge
OUT_PX = 600

# Above this crop width in source pixels, read the overview instead of the tiles. Set
# just above 4x the output size: past that the tiles carry detail the output cannot show.
TILE_LIMIT = OUT_PX * 4

BACKDROP = (13, 22, 30)   # the basemaps' own out-of-map colour, for crops running off it

MARKER_FILL = (233, 69, 62)
PLAYER_FILL = (74, 194, 255)
INK = (255, 255, 255)
SHADOW = (0, 0, 0)


@dataclass(frozen=True)
class Region:
    name: str
    priority: int
    directory: Path
    tile_size: int
    tile_cols: int
    tile_rows: int
    image_w: int
    image_h: int
    overview_px: int
    map_x_left: float
    map_x_right: float
    map_y_top: float
    map_y_bottom: float

    def contains(self, mx: float, my: float) -> bool:
        return (self.map_x_left <= mx <= self.map_x_right
                and self.map_y_bottom <= my <= self.map_y_top)

    def to_pixels(self, mx: float, my: float) -> tuple[float, float]:
        """Map units to pixels in the full-resolution basemap.

        map_y runs *up* the image - the ingest measured the row axis as inverted - so the
        row term subtracts from the top rather than adding from the bottom.
        """
        col = ((mx - self.map_x_left) / (self.map_x_right - self.map_x_left)
               * self.image_w)
        row = ((self.map_y_top - my) / (self.map_y_top - self.map_y_bottom)
               * self.image_h)
        return col, row

    @property
    def px_per_unit(self) -> float:
        return self.image_w / (self.map_x_right - self.map_x_left)


class MapAssets:
    """Published map tiles and Pal icons, loaded lazily.

    Tiles rather than whole basemaps because each region is 8192x8192 - a 268 MB decode
    for a 600 px crop. A crop touches at most a handful of 512 px tiles, and they are
    cached, so a second query in the same area costs no file IO at all.
    """

    def __init__(self, root: Path, regions: list[Region], icons: dict[str, str],
                 resource_icons: dict[str, str] | None = None):
        self.root = root
        self.regions = sorted(regions, key=lambda r: -r.priority)
        self.icons = icons
        self.resource_icons = resource_icons or {}

    @classmethod
    def load(cls, root: Path) -> "MapAssets | None":
        """Return None when assets are absent, so the bot runs without them."""
        manifest = root / "manifest.json"
        if Image is None or not manifest.exists():
            return None
        data = json.loads(manifest.read_text(encoding="utf-8"))
        regions = [
            Region(name=r["region"], priority=r["priority"], directory=root / r["dir"],
                   tile_size=r["tile_size"], tile_cols=r["tile_cols"],
                   tile_rows=r["tile_rows"], image_w=r["image_w"], image_h=r["image_h"],
                   overview_px=r.get("overview_px", 0),
                   map_x_left=r["map_x_left"], map_x_right=r["map_x_right"],
                   map_y_top=r["map_y_top"], map_y_bottom=r["map_y_bottom"])
            for r in data["map_regions"]]
        return cls(root, regions, data.get("icons", {}),
                   data.get("resource_icons", {}))

    def _file(self, index: dict[str, str], key: str) -> Path | None:
        rel = index.get(key)
        path = self.root / rel if rel else None
        return path if path and path.exists() else None

    def icon(self, canonical: str) -> Path | None:
        return self._file(self.icons, canonical)

    def resource_icon(self, resource: str) -> Path | None:
        """The item's inventory icon.

        Worth being precise about what this shows: the *material*, as it appears in your
        pack - not the rock in the world. The game carries no 2D art for a node's
        appearance at all; map objects have no icon field, only meshes. So this narrows
        "what am I looking for" without answering it outright.
        """
        return self._file(self.resource_icons, resource)

    @lru_cache(maxsize=64)
    def _tile(self, directory: str, col: int, row: int):
        path = Path(directory) / f"{col}_{row}.jpg"
        return Image.open(path).convert("RGB") if path.exists() else None

    @lru_cache(maxsize=4)
    def _overview(self, directory: str):
        path = Path(directory) / "overview.jpg"
        return Image.open(path).convert("RGB") if path.exists() else None

    def region_for(self, points: list[tuple[float, float]]) -> Region | None:
        """The highest-priority region holding *every* point, or None.

        All-or-nothing on purpose. Picking the region that holds the most would crop away
        the rest while the card text still lists them, and a picture that shows two of
        three answers without saying so is worse than no picture.
        """
        for region in self.regions:
            if all(region.contains(mx, my) for mx, my in points):
                return region
        return None

    def _compose(self, region: Region, box: tuple[int, int, int, int], out_px: int):
        """The requested pixel box, rendered at `out_px`, from whichever level is cheaper.

        A crop wider than TILE_LIMIT is taken from the overview instead of the tiles. The
        detail is not lost - there was none to keep. At that width one output pixel is
        already several source pixels, so assembling 64 tiles and resizing 12.7
        megapixels buys a picture indistinguishable from the small one and costs 472 ms
        against 8 ms.
        """
        left, top, right, bottom = box
        span = right - left

        if span > TILE_LIMIT and region.overview_px:
            overview = self._overview(str(region.directory))
            if overview is not None:
                shrink = region.overview_px / region.image_w
                small = (int(left * shrink), int(top * shrink),
                         int(right * shrink), int(bottom * shrink))
                return overview.resize((out_px, out_px), Image.LANCZOS, box=small)

        out = Image.new("RGB", (span, bottom - top), BACKDROP)
        size = region.tile_size
        for row in range(top // size, (bottom - 1) // size + 1):
            for col in range(left // size, (right - 1) // size + 1):
                if not (0 <= col < region.tile_cols and 0 <= row < region.tile_rows):
                    continue
                tile = self._tile(str(region.directory), col, row)
                if tile is not None:
                    out.paste(tile, (col * size - left, row * size - top))
        return out.resize((out_px, out_px), Image.LANCZOS)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10
        return ImageFont.load_default()


def _text(draw, xy, text, font, fill=INK, anchor="la"):
    """Draw with a 1 px shadow, because the basemap under it is arbitrary."""
    x, y = xy
    draw.text((x + 1, y + 1), text, font=font, fill=SHADOW, anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


def _scale_bar(draw, size: int, px_per_unit: float, font) -> None:
    """A bar in metres, since map units mean nothing to a player walking there.

    1 map unit is ~4.6 m (data/coord_transform.json), so the bar answers "is this a
    thirty-second walk or a five-minute one" - the question a crop with no fixed zoom
    otherwise leaves open.
    """
    metres_per_unit = 4.6
    for metres in (100, 250, 500, 1000, 2000, 5000):
        width = metres / metres_per_unit * px_per_unit
        if width >= size * 0.18:
            break
    label = f"{metres} m" if metres < 1000 else f"{metres / 1000:g} km"
    x, y = 12, size - 18
    draw.line([(x, y), (x + width, y)], fill=SHADOW, width=4)
    draw.line([(x, y), (x + width, y)], fill=INK, width=2)
    for end in (x, x + width):
        draw.line([(end, y - 4), (end, y + 4)], fill=INK, width=2)
    _text(draw, (x, y - 18), label, font)


def render(assets: MapAssets, points: list[tuple[float, float, str]],
           near: tuple[float, float] | None = None,
           out_px: int = OUT_PX) -> bytes | None:
    """A cropped basemap with the results marked, as JPEG bytes, or None.

    `points` are (map_x, map_y, label) in the order the card lists them, so the numbers
    on the picture and the numbers in the text are the same numbers.
    """
    if Image is None or not points:
        return None

    coords = [(x, y) for x, y, _ in points]
    if near is not None:
        coords.append(near)

    region = assets.region_for(coords)
    if region is None:
        # Either outside every published map, or straddling two of them. Both are cases
        # where the honest output is the text card alone.
        return None

    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    extent = max(max(xs) - min(xs), max(ys) - min(ys))
    half = max(extent * (0.5 + PADDING), MIN_EXTENT / 2)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2

    left, bottom = region.to_pixels(cx - half, cy - half)
    right, top = region.to_pixels(cx + half, cy + half)
    box = (int(left), int(top), int(right), int(bottom))
    span = max(box[2] - box[0], box[3] - box[1], 1)
    box = (box[0], box[1], box[0] + span, box[1] + span)

    image = assets._compose(region, box, out_px)
    scale = out_px / span
    draw = ImageDraw.Draw(image)

    def place(mx, my):
        px, py = region.to_pixels(mx, my)
        return (px - box[0]) * scale, (py - box[1]) * scale

    if near is not None:
        # Drawn first, so a numbered answer sitting on top of the player still wins the
        # pixel - the answer is what the card is for. The halo is what keeps it findable
        # anyway: a bare 14px dot disappeared against the node markers' 24px double
        # rings, and "where am I relative to these" is half of what the picture answers.
        x, y = place(*near)
        halo_font = _font(12)
        for radius, colour, width in ((17, SHADOW, 4), (17, PLAYER_FILL, 2)):
            draw.ellipse([x - radius, y - radius, x + radius, y + radius],
                         outline=colour, width=width)
        draw.ellipse([x - 9, y - 9, x + 9, y + 9], outline=SHADOW, width=3)
        draw.ellipse([x - 8, y - 8, x + 8, y + 8], fill=PLAYER_FILL, outline=INK, width=2)
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=INK)
        # Labelled, because a blue disc among red ones reads as another result to a
        # reader who has not been told otherwise.
        _text(draw, (x, y + 21), "you", halo_font, anchor="ma")

    label_font = _font(15)
    for i, (mx, my, _) in enumerate(points, 1):
        x, y = place(mx, my)
        # A white ring inside a dark one. The game's own map art uses red for dungeon
        # and camp markers, so a bare red disc reads as scenery over the volcano; the
        # double ring is what separates our answer from the basemap under it.
        draw.ellipse([x - 12, y - 12, x + 12, y + 12], outline=SHADOW, width=3)
        draw.ellipse([x - 11, y - 11, x + 11, y + 11], fill=MARKER_FILL, outline=INK,
                     width=2)
        _text(draw, (x, y), str(i), label_font, anchor="mm")

    small = _font(13)
    _scale_bar(draw, out_px, region.px_per_unit * scale, small)
    # Name the region, because two crops of different islands otherwise look alike and
    # the card gives no other cue which map is being shown.
    _text(draw, (out_px - 12, 10), region.name, small, anchor="ra")

    buffer = io.BytesIO()
    # JPEG, and it is the whole latency story: an optimised PNG of this crop is 395 KB
    # and 79 ms to encode, against 60 KB and 2 ms here - most of the render, spent on a
    # basemap that is photographic art. Nothing the player acts on is at risk from the
    # compressor, since every number on the picture is also printed on the card.
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    return buffer.getvalue()
