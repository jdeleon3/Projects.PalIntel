"""Card artwork — mostly tests of when it refuses to draw.

The picture is decoration; the refusals are the design. A map crop is the one output in
this project that can be confidently wrong while looking authoritative, because a marker
on the wrong island reads exactly like a marker on the right one. So the cases worth
pinning are the ones where `render` must return None, and the case where a card built
without artwork stays clean.

Tests needing the published assets skip when they are absent: data/<version>/assets/ is
extracted from the player's own game install and gitignored (Docs/03-data-ingestion.md
§7), so a fresh checkout legitimately has none.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from palintel.artwork import Artwork
from palintel.cards import Card
from palintel.execution import ResourceResult
from palintel.knowledge import ResourceNode
from palintel.mapcard import MapAssets, Region, render

ASSETS = Path(__file__).resolve().parents[1] / "data" / "1.0.2" / "assets"


@pytest.fixture(scope="module")
def assets() -> MapAssets:
    loaded = MapAssets.load(ASSETS)
    if loaded is None:
        pytest.skip("no published assets - run tools/ingest/build_assets.py")
    return loaded


# ---------------------------------------------------------------- attachment naming

def test_a_card_without_artwork_names_no_attachments():
    assert Card(title="Coal locations").attachments() == {}
    embed = Card(title="Coal locations").to_embed()
    assert embed["image"] is None and embed["thumbnail"] is None


def test_each_card_in_a_message_gets_its_own_filename():
    """Two cards sharing an attachment name would both show the first one's picture.

    A Paldeck slot with a variant renders two cards in one message, and Discord matches
    `attachment://` by filename alone - so Menasting and Menasting Terra would be given
    the same map of whichever was built first.
    """
    a = Card(title="Menasting", image=b"jpeg-a")
    b = Card(title="Menasting Terra", image=b"jpeg-b")
    assert a.attachments(0)["image"] != b.attachments(1)["image"]
    assert a.to_embed(0)["image"]["url"] == "attachment://map0.jpg"
    assert b.to_embed(1)["image"]["url"] == "attachment://map1.jpg"


def test_an_icon_and_a_map_are_separate_attachments():
    card = Card(title="Chillet locations", image=b"jpeg", thumbnail=Path("Chillet.png"))
    names = card.attachments(0)
    assert names["image"] != names["thumbnail"]
    embed = card.to_embed(0)
    assert embed["image"]["url"].endswith("map0.jpg")
    assert embed["thumbnail"]["url"].endswith("icon0.png")


# ------------------------------------------------------------------------- refusals

def _region(name: str, priority: int, x0: float, x1: float, y0: float, y1: float):
    return Region(name=name, priority=priority, directory=Path("."), tile_size=512,
                  tile_cols=16, tile_rows=16, image_w=8192, image_h=8192,
                  overview_px=1024, map_x_left=x0, map_x_right=x1,
                  map_y_top=y1, map_y_bottom=y0)


def test_the_higher_priority_region_wins_where_two_overlap():
    """Bounds overlap slightly, so priority is what decides - not iteration order."""
    main = _region("MainMap", 0, -100, 100, -100, 100)
    tree = _region("Tree", 1, 50, 300, 50, 300)
    assets = MapAssets(Path("."), [main, tree], {})
    assert assets.region_for([(75.0, 75.0)]).name == "Tree"
    assert assets.region_for([(0.0, 0.0)]).name == "MainMap"


def test_a_coordinate_on_no_published_map_gets_no_map(assets):
    """The World Tree lies outside the main island's rectangle, and vice versa.

    Somewhere in neither is not a place to draw a best guess: clamping it into the
    nearest region would put a marker on land that has nothing to do with the answer.
    """
    assert render(assets, [(-99999.0, -99999.0, "coal")]) is None


def test_points_straddling_two_maps_get_no_map(assets):
    """Cropping to one region would drop the rest while the card still lists them.

    The failure this prevents is subtler than a wrong marker: the picture would be
    correct as far as it went, and would silently disagree with the text above it about
    how many answers there are.
    """
    main = next(r for r in assets.regions if r.name == "MainMap")
    tree = next(r for r in assets.regions if r.name == "Tree")
    in_main = ((main.map_x_left + main.map_x_right) / 2,
               (main.map_y_top + main.map_y_bottom) / 2)
    in_tree = ((tree.map_x_left + tree.map_x_right) / 2,
               (tree.map_y_top + tree.map_y_bottom) / 2)
    assert assets.region_for([in_main]) is not None
    assert assets.region_for([in_tree]) is not None
    assert render(assets, [(*in_main, "coal"), (*in_tree, "coal")]) is None


def test_no_points_is_no_map(assets):
    assert render(assets, []) is None


# ------------------------------------------------------------------------- drawing

def _result(coords: list[tuple[float, float]]) -> ResourceResult:
    nodes = [ResourceNode(node_id=f"n{i}", resource="coal", map_x=x, map_y=y,
                          node_count=3, spread=0.0, min_player_level=None,
                          danger=None, area_hint=None)
             for i, (x, y) in enumerate(coords)]
    return ResourceResult(resource="coal", nodes=nodes, near=None,
                          level_filtered=False, total_available=len(nodes))


def test_a_resource_result_inside_one_region_is_illustrated(assets):
    main = next(r for r in assets.regions if r.name == "MainMap")
    cx = (main.map_x_left + main.map_x_right) / 2
    cy = (main.map_y_top + main.map_y_bottom) / 2
    card = Card(title="Coal locations")
    draw = Artwork(assets).illustrate_resource(card, _result([(cx, cy), (cx + 40, cy - 30)]))
    assert card.image is None, "planning must not render - that is the whole point"
    draw()
    assert card.image is not None
    assert card.image[:2] == b"\xff\xd8"        # JPEG SOI


def test_a_wide_spread_still_draws_and_stays_cheap(assets):
    """Spawn areas 1,000 map units apart used to cost 472ms and 64 tile decodes.

    The answer is legitimate - some Pals really are found in two corners of the map - so
    refusing would drop a correct result. The second zoom level is what makes it cheap
    instead.
    """
    main = next(r for r in assets.regions if r.name == "MainMap")
    cx = (main.map_x_left + main.map_x_right) / 2
    cy = (main.map_y_top + main.map_y_bottom) / 2
    wide = render(assets, [(cx - 500, cy - 500, "pal"), (cx + 500, cy + 500, "pal")])
    assert wide is not None and wide[:2] == b"\xff\xd8"


def test_render_and_upload_are_reported_apart():
    """One "artwork" figure could not say which half moved.

    Render is local CPU and upload is a Discord round trip - different costs, different
    fixes. The status card is the only place this is visible from Discord, which is
    where the person asking is standing.
    """
    from palintel.activity import ART_KINDS, ActivityLog
    from palintel.cards import status_card

    log = ActivityLog()
    log.timed("text", 900.0)
    for ms in (8.0, 12.0, 25.0):
        log.timed("art_render", ms, "65KB where's the coal")
        log.timed("art_post", ms * 20, "65KB where's the coal")

    assert set(ART_KINDS) == {"art_render", "art_post"}
    line = next(l for l in status_card(log, voice="off").lines if "artwork" in l)
    assert "render" in line and "post" in line
    assert "(n=3)" in line
    # Named as happening after the answer: the whole claim of ADR-0017 is that this
    # time is not on the graded path, and a latency figure with no such label reads as
    # though it were.
    assert "after the answer" in line


def test_artwork_timings_stay_out_of_the_graded_total():
    """A slow upload must not make the answer look slow. Different promises."""
    from palintel.activity import DECLINE_KINDS, GRADED_KINDS, TIMED_KINDS

    assert "art_render" in TIMED_KINDS and "art_post" in TIMED_KINDS
    assert not {"art_render", "art_post"} & set(GRADED_KINDS + DECLINE_KINDS)


def test_a_resource_card_gets_a_map_and_no_thumbnail(assets):
    """The material's inventory icon was tried on these cards and dropped.

    It shows what the item looks like in your pack; recognising a deposit needs the rock
    in the world, which the game ships no 2D art for. A picture answering a question
    nobody asked still costs the reader a glance, so the slot stays empty.
    """
    main = next(r for r in assets.regions if r.name == "MainMap")
    cx = (main.map_x_left + main.map_x_right) / 2
    cy = (main.map_y_top + main.map_y_bottom) / 2
    card = Card(title="Quartz locations")
    Artwork(assets).illustrate_resource(card, _result([(cx, cy)]))()
    assert card.image is not None
    assert card.thumbnail is None


def test_artwork_stays_off_unless_asked():
    assert Artwork.load(ASSETS, maps=False, icons=False) is None


def test_missing_assets_do_not_stop_the_bot(tmp_path):
    """Absent artwork degrades to a text card rather than failing to start.

    The assets come from the player's own install; refusing to run without them would
    trade a working bot for a decorative one.
    """
    assert Artwork.load(tmp_path / "nothing", maps=True, icons=True) is None


def test_a_render_failure_leaves_the_answer_intact(assets, monkeypatch):
    """The card is already correct before artwork is attempted, and stays that way."""
    import palintel.artwork as module
    monkeypatch.setattr(module, "render",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("bad tile")))
    card = Card(title="Coal locations", lines=["**1. (20, -153)**"])
    Artwork(assets).illustrate_resource(card, _result([(0.0, 0.0)]))()
    assert card.image is None
    assert card.lines == ["**1. (20, -153)**"]
