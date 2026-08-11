"""Published nodes are overworld nodes — the claim the dataset makes about itself.

`resource_nodes.json` has carried `"scope": "overworld only - dungeon and instanced maps
are NOT included"` since it was created, and until 2026-08-10 that was false. Cave and
dungeon contents live in World Partition cells at `L15_X0_Y0` - one cell at the grid
origin, because they are authored in their own local space rather than placed on the map
- and their coordinates ran through the overworld transform as if they were positions.
16.4% of the dataset, shipped through Phase 1 and Phase 2 as answerable coordinates.

It was invisible in text. "(224, -600) | 1 deposit | 114 units away" reads exactly like a
real answer, and the only reason it surfaced is that a map crop drew the marker in open
water. These are the two tests that would have caught it: one on the rule, one on the
data, deliberately by different mechanisms - the rule test cannot see a new way for a
coordinate to be wrong, and the data test cannot say why.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "ingest"))

from build_resource_nodes import is_overworld  # noqa: E402

DATA = REPO / "data" / "1.0.2"
ASSETS = DATA / "assets"


# --------------------------------------------------------------------------- the rule

def _at(cell: str, hops: int = 0) -> dict:
    return {"cell": cell, "owner_hops": hops}


def test_spatially_partitioned_cells_are_overworld():
    assert is_overworld(_at("MainGrid_L0_X-1_Y-1_DLA2255F0E"))
    assert is_overworld(_at("MainGrid_L0_X0_Y0_DL0"))
    assert is_overworld(_at("CloseRange_L0_X12_Y-3_DL0"))


def test_the_dungeon_cell_is_not_when_nothing_resolved_it():
    """`L15_X0_Y0` holds contents authored in their own space, unless an Owner says
    otherwise. Unresolved, the coordinate is dungeon-local and means nothing on the map."""
    assert not is_overworld(_at("MainGrid_L15_X0_Y0_DL199241D7"))
    assert not is_overworld(_at("MainGrid_L15_X0_Y0_DLED2A0377"))


def test_an_owner_resolved_placement_is_a_world_position():
    """633 actors in that cell carry an Owner, and composing it puts them back in world
    space - 76.5% land on terrain against 79.4% for L0 and 46.3% for the unresolved ones.

    Excluding them was a real miss, found by standing on one: a card said the nearest coal
    was (198, -231) with coal actually at (230, -218), which is this cell's placement
    (230.7, -217.0) at owner_hops=1. It cost 171 coal deposits.
    """
    assert is_overworld(_at("MainGrid_L15_X0_Y0_DLA2255F0E", hops=1))


def test_an_unreadable_cell_name_is_excluded():
    """Excluded rather than assumed overworld, whatever its owner chain says.

    If World Partition naming changes upstream, the failure should cost coverage - a
    visible drop in node counts - rather than silently readmitting dungeon coordinates.
    """
    assert not is_overworld(_at(""))
    assert not is_overworld(_at("SomeFutureNamingScheme_Cell_42", hops=1))


# ---------------------------------------------------------------------------- the data

@pytest.fixture(scope="module")
def published() -> list[dict]:
    path = DATA / "resource_nodes.json"
    if not path.exists():
        pytest.skip("no published dataset - run tools/ingest/build_resource_nodes.py")
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["nodes"] if isinstance(data, dict) else data


def test_coal_clusters_are_not_in_the_ocean(published):
    """The property the rule exists to protect, checked without reference to the rule.

    Sampled against the published basemap rather than the cell name, so a *different*
    mechanism producing the same symptom - a bad transform, a clustering anchor landing
    offshore - still fails here. Coal because it was the worst affected: two thirds of
    coal deposits were cave coal, and it was a coal card that exposed this.

    The bar is 85%, not 100%. The land test is a crude colour rule that reads dark
    volcanic terrain and cave mouths as water, and some deposits genuinely sit on
    shoreline. Before the fix coal scored 73.0%; after, 92.9%.
    """
    pytest.importorskip("PIL")
    from PIL import Image

    from palintel.mapcard import MapAssets

    assets = MapAssets.load(ASSETS)
    if assets is None:
        pytest.skip("no published assets - run tools/ingest/build_assets.py")
    region = next((r for r in assets.regions if r.name == "MainMap"), None)
    overview = assets._overview(str(region.directory)) if region else None
    if overview is None:
        pytest.skip("no region overview in the published assets")

    shrink = region.overview_px / region.image_w
    pixels = overview.load()
    size = region.overview_px

    def on_land(mx: float, my: float) -> bool:
        px, py = region.to_pixels(mx, my)
        x = min(size - 1, max(0, int(px * shrink)))
        y = min(size - 1, max(0, int(py * shrink)))
        r, g, b = pixels[x, y]
        return r > b or max(r, g, b) > 120

    coal = [n for n in published if n["resource"] == "coal"
            and region.contains(n["map_x"], n["map_y"])]
    assert coal, "no coal clusters inside the main map at all"
    land = sum(on_land(n["map_x"], n["map_y"]) for n in coal) / len(coal)
    assert land >= 0.85, (
        f"only {land:.1%} of {len(coal)} coal clusters land on terrain. Dungeon-interior "
        f"coordinates are being published as overworld positions again - check "
        f"is_overworld in tools/ingest/build_resource_nodes.py against the cell names in "
        f"data/raw/placements.json.")


def test_the_dataset_says_it_is_overworld_only(published):
    """The claim and the filter are in different files; this is what ties them."""
    data = json.loads((DATA / "resource_nodes.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        pytest.skip("dataset carries no metadata header")
    assert "overworld" in data.get("scope", "").lower()
