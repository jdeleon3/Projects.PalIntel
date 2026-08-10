"""Validation of the shipped Pal spawn dataset.

These are data tests, not code tests: they run against `data/1.0.2/pal_spawns.json` as
published and fail if a rebuild changes its shape or degrades its agreement with the
independent sources it was joined from. Phase 1 learned this the expensive way - a
dataset that is silently wrong produces cards that are confidently wrong, and nothing
downstream can tell.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from palintel.knowledge import Lexicon
from palintel.saves import Transform

VERSION = "1.0.2"
DATA = Path("data") / VERSION


@pytest.fixture(scope="module")
def spawns() -> dict:
    return json.loads((DATA / "pal_spawns.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lexicon() -> Lexicon:
    return Lexicon(DATA / "lexicon.json")


# --- structure -------------------------------------------------------------------

def test_every_area_names_a_pal_in_the_lexicon(spawns, lexicon):
    """The router selects from the lexicon's enum, so an area it cannot name is dead."""
    known = set(lexicon.pals())
    unknown = sorted({a["pal"] for a in spawns["areas"]} - known)
    assert not unknown, f"areas reference Pals absent from the lexicon: {unknown}"


def test_coverage_partitions_the_paldeck(spawns, lexicon):
    """Every Pal is either locatable or explicitly listed as not. No third state.

    `pals_without_areas` is what lets the answer be "Jetragon does not spawn in the
    overworld" instead of "not found", which are different claims and only one is true.
    """
    with_areas = {a["pal"] for a in spawns["areas"]}
    without = set(spawns["pals_without_areas"])
    assert with_areas & without == set()
    assert with_areas | without == set(lexicon.pals())


def test_clustering_guarantee_holds(spawns):
    """Leader clustering bounds cluster DIAMETER at 2*radius, measured from any member."""
    limit = 2 * spawns["cluster_radius_map_units"] + 1e-6
    over = [a["area_id"] for a in spawns["areas"] if a["spread_map_units"] > limit]
    assert not over, f"{len(over)} area(s) wider than the cluster diameter: {over[:3]}"


def test_no_area_is_a_pile_of_collapsed_coordinates(spawns):
    """Many points at zero spread is an unresolved parent transform, not a spawn cluster."""
    bad = [a["area_id"] for a in spawns["areas"]
           if a["spawn_points"] >= 8 and a["spread_map_units"] == 0.0]
    assert not bad, f"collapsed coordinates in {bad[:3]}"


def test_levels_and_shares_are_in_range(spawns):
    for a in spawns["areas"]:
        assert 1 <= a["level_min"] <= a["level_max"] <= 80, a["area_id"]
        assert 0.0 < a["encounter_share"] <= 1.0, a["area_id"]


def test_pvp_arena_sheets_are_excluded(spawns):
    """They cost no coverage and dominate density for common early Pals. See the ingest."""
    cited = [a["area_id"] for a in spawns["areas"]
             if any("PvP" in s for s in a["sheets"])]
    assert not cited, f"PvP sheets reached the dataset: {cited[:3]}"


# --- agreement with independent sources -------------------------------------------

def test_coordinates_round_trip_through_the_shipped_transform(spawns):
    """World and map coords are stored together; re-deriving one checks they agree."""
    t = Transform.load()
    assert spawns["transform_id"] == t.transform_id
    for a in spawns["areas"][:200]:
        mx, my = t.to_map(a["world"]["x"], a["world"]["y"])
        assert abs(mx - a["map_x"]) < 1.0 and abs(my - a["map_y"]) < 1.0, a["area_id"]


def test_alpha_areas_agree_with_the_boss_data_table(spawns, lexicon):
    """The cross-check that caught the sheet route missing main-island field bosses.

    Two extraction paths reach the same alphas: World Partition actors composed up an
    Owner chain, and DT_BossSpawnerLoactionData read directly. Both are transformed by
    the same fit, so it cancels - what this measures is the actor extraction. It agreed
    to a median of 0.0 map units over 74 shared spawners when the merge was written.

    Both sources are now merged into the dataset, so this asserts the weaker but still
    meaningful property: every mapped table row is represented by an alpha area at
    essentially its own coordinates.
    """
    by_id = {i.lower(): p for p in lexicon.pals()
             for i in [p]}  # canonical names round-trip through themselves
    raw = json.loads(Path("data/raw/boss_spawner_locations.json")
                     .read_text(encoding="utf-8"))["Rows"]
    lex_ids = json.loads((DATA / "lexicon.json").read_text(encoding="utf-8"))
    by_internal = {i.lower(): p["canonical"]
                   for p in lex_ids["pals"] for i in p["internal_ids"]}
    t = Transform.load()

    alphas: dict[str, list[tuple[float, float]]] = {}
    for a in spawns["areas"]:
        if a["kind"] == "alpha":
            alphas.setdefault(a["pal"], []).append((a["map_x"], a["map_y"]))

    checked, worst = 0, 0.0
    for row in raw.values():
        if row["CharacterID"] == "None":
            continue
        base = re.sub(r"^BOSS_", "", row["CharacterID"], flags=re.I)
        canon = by_internal.get(base.lower())
        if canon is None:
            continue
        mx, my = t.to_map(row["Location"]["X"], row["Location"]["Y"])
        here = alphas.get(canon)
        assert here, f"{canon} is in the boss table but has no alpha area"
        d = min(math.dist((mx, my), p) for p in here)
        worst = max(worst, d)
        checked += 1

    assert checked >= 85, f"only {checked} boss rows checked; the table shrank"
    # One cluster radius: a table row must land inside the area it seeded, never merely
    # somewhere on the same island.
    assert worst <= spawns["cluster_radius_map_units"], (
        f"worst boss-table disagreement {worst:.1f} map units")


def test_the_desert_anubis_alpha_is_where_players_find_it(spawns):
    """One hand-checked landmark. Guards the whole join against a plausible-looking
    rebuild that has quietly shifted everything."""
    anubis = [a for a in spawns["areas"] if a["pal"] == "Anubis" and a["kind"] == "alpha"]
    assert len(anubis) == 1
    assert math.dist((anubis[0]["map_x"], anubis[0]["map_y"]), (-134.0, -94.0)) < 5.0
    assert anubis[0]["level_min"] == anubis[0]["level_max"] == 55
