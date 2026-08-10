"""Validation of the shipped resource node dataset and its derived fields.

Data tests, like tests/test_pal_spawns.py: they run against `data/1.0.2/` as published
and fail if a rebuild changes the resource set, breaks its agreement with the lexicon, or
produces difficulty values the documented rule cannot have produced.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from palintel.cards import MAX_NAMED_OPTIONS, decline_card
from palintel.knowledge import KnowledgeBase
from palintel.tools import Decline

VERSION = "1.0.2"
DATA = Path("data") / VERSION


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load(VERSION)


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads((DATA / "resource_nodes.json").read_text(encoding="utf-8"))


# --- the derived resource set ------------------------------------------------------

def test_the_lexicon_and_the_nodes_agree_on_what_a_resource_is(kb: KnowledgeBase):
    """The reason tools/ingest/_resources.py exists.

    A resource the lexicon knows but the dataset lacks is a query that resolves to an
    entity with no data. One the dataset has but the lexicon lacks is a node nobody can
    name. Only `crude_oil` is deliberately in the first group.
    """
    named = set(kb.lexicon.resources())
    placed = {n.resource for n in kb.nodes}
    assert placed <= named, f"nodes for unnameable resources: {sorted(placed - named)}"
    assert named - placed == {"crude_oil"}, sorted(named - placed)


def test_the_phase_1_resources_survived_the_widening(kb: KnowledgeBase):
    """The A5 transcripts, the lexicon aliases and every recorded eval use these ids."""
    assert {"ore", "coal", "sulfur", "quartz"} <= {n.resource for n in kb.nodes}


def test_sky_island_and_world_tree_ore_are_not_ore(kb: KnowledgeBase):
    """The defect that hand-mapping produced and derivation caught.

    BP_PalMapObjectSpawner_SkyIslandOre_C yields Soralite and _WorldTreeOre_C yields
    Paloxite. Both shipped as `ore` for the whole of Phase 1 - 306 clusters telling a
    player they had found ore when they had not.
    """
    by_resource = {n.resource for n in kb.nodes}
    assert {"soralite", "paloxite"} <= by_resource

    # And the hint still says where they are, which is the job it should have had all along.
    for resource, hint in (("soralite", "sky_island"), ("paloxite", "world_tree")):
        nodes = [n for n in kb.nodes if n.resource == resource]
        assert nodes and all(n.area_hint == hint for n in nodes)


def test_no_collectibles_leaked_into_the_resource_set(kb: KnowledgeBase):
    """The item-category cut keeps lotuses, Dog Coins and Kinship Peaches out."""
    named = set(kb.lexicon.resources())
    for junk in ("power_lotus_s", "dog_coin", "beautiful_flower", "kinship_peach"):
        assert junk not in named


# --- derived difficulty ------------------------------------------------------------

def test_derived_levels_are_in_the_documented_range(raw: dict):
    """03-data-ingestion.md section 6: derived min_player_level within 1-60."""
    for n in raw["nodes"]:
        if n["min_player_level"] is not None:
            assert 1 <= n["min_player_level"] <= 60, n["node_id"]


def test_the_rule_and_its_inputs_are_published(raw: dict):
    """An answer has to be traceable to the rule that produced it, so the rule ships."""
    assert raw["difficulty_rule"]
    inputs = raw["difficulty_inputs"]
    assert inputs["local_radius_map_units"] > 0
    assert "percentile" in inputs["local_wild_level"]


def test_difficulty_matches_the_formula_it_claims(raw: dict):
    """Recompute it. A published rule that does not describe the data is worse than none."""
    for n in raw["nodes"]:
        wild = n["local_wild_level"]
        if wild is None:
            assert n["min_player_level"] is None and n["danger"] is None
            continue
        danger = "low" if wild <= 20 else "moderate" if wild <= 40 else "high"
        assert n["danger"] == danger, n["node_id"]
        expected = min(60, max(1, math.ceil(wild * 0.8) + (5 if danger == "high" else 0)))
        assert n["min_player_level"] == expected, n["node_id"]


def test_the_starting_area_is_not_rated_end_game(kb: KnowledgeBase):
    """Taking the literal max of nearby levels made the level 1-7 starter zone read as
    level 35, because a Mammorest spawns there on a 1% roll. The weighted percentile is
    what fixed it, and this is the case that showed the bug."""
    starter = min(kb.nodes, key=lambda n: math.dist((n.map_x, n.map_y), (214.0, -485.0)))
    assert starter.danger == "low"
    assert starter.min_player_level is not None and starter.min_player_level <= 15


def test_late_game_regions_are_still_rated_dangerous(kb: KnowledgeBase):
    """The fix must not have flattened everything into "low"."""
    for x, y in ((-560.0, 240.0), (-1290.0, -620.0)):
        node = min(kb.nodes, key=lambda n: math.dist((n.map_x, n.map_y), (x, y)))
        assert node.danger == "high"
        assert node.min_player_level >= 40


# --- presentation ------------------------------------------------------------------

def test_a_decline_names_a_readable_number_of_options(kb: KnowledgeBase):
    """Eighteen resources do not fit on a card that is already saying "I didn't catch
    that". The list is ordered by how much data backs each, so truncation keeps the
    useful end."""
    options = sorted({n.resource for n in kb.nodes})
    card = decline_card(Decline(reason="no resource identified", known_options=options))
    line = next(line for line in card.lines if "I can currently find" in line)
    assert line.count(",") < MAX_NAMED_OPTIONS
    assert f"and {len(options) - MAX_NAMED_OPTIONS} more" in line
