"""Q4 base siting — the circle, the ranking, and the claim the card must never make.

This class is thinner than the roadmap's Q4 on purpose: the design was twenty
hand-curated sites with a hand-scored `flatness_score`, and what is built is the half the
game states. The tests are therefore mostly about the boundary between the two.

**The one that matters most is `test_the_card_never_claims_the_ground_is_buildable`.**
Nothing found in the pak says whether a spot is flat, underwater, or inside a no-build
zone, so a site here is where the resources are. A card that let that read as "build
here" would produce this project's signature failure in a new place: an in-bounds,
correctly transformed, entirely wrong coordinate.
"""
from __future__ import annotations

import math

import pytest

from palintel import cards
from palintel.execution import suggest_base_sites
from palintel.knowledge import KnowledgeBase, ResourceNode


def node(node_id, resource, x, y, count=1) -> ResourceNode:
    return ResourceNode(node_id=node_id, resource=resource, map_x=x, map_y=y,
                        node_count=count, spread=0.0, min_player_level=None,
                        danger=None, area_hint=None)


@pytest.fixture
def kb() -> KnowledgeBase:
    """A hand-laid map, so these tests state their own geometry.

    Radius 10. Cluster A is coal+ore together; cluster B is a bigger coal pile alone,
    far away; cluster C is ore alone, also far away.
    """
    nodes = [
        node("a-coal", "coal", 0, 0, count=5),
        node("a-ore", "ore", 6, 0, count=4),
        node("b-coal", "coal", 500, 0, count=40),
        node("c-ore", "ore", -500, 0, count=40),
    ]
    return KnowledgeBase(game_version="test", lexicon=None, nodes=nodes,
                         base_radius=10.0)


# ------------------------------------------------------------------ the circle

def test_only_what_is_inside_the_radius_counts(kb):
    result = suggest_base_sites(kb, ["coal"], limit=5)
    top = next(s for s in result.sites if (s.map_x, s.map_y) == (0, 0))
    # The ore is 6 away and inside; the far coal is 500 away and is not.
    assert top.covered == {"coal": 5}
    assert dict(top.also) == {"ore": 4}


def test_a_site_reaching_everything_outranks_a_bigger_one_that_does_not(kb):
    """The whole point of the class. Cluster B has 40 coal and no ore; cluster A has 5
    coal and 4 ore. Asked for both, A wins - a base reaching two of two answers the
    question and forty deposits of one does not."""
    result = suggest_base_sites(kb, ["coal", "ore"], limit=5)
    assert (result.sites[0].map_x, result.sites[0].map_y) == (0, 0)
    assert result.sites[0].complete


def test_a_site_that_misses_one_names_what_it_missed(kb):
    """Absence must not have to be counted. "A base for coal and ore" that only reaches
    coal is a different answer from one that reaches both."""
    result = suggest_base_sites(kb, ["coal", "ore"], limit=5)
    far = next(s for s in result.sites if s.map_x == 500)
    assert far.missing == ("ore",)


def test_deposits_break_the_tie_once_coverage_is_equal(kb):
    result = suggest_base_sites(kb, ["coal"], limit=5)
    assert result.sites[0].deposits == 40      # cluster B, which is bigger


def test_overlapping_centres_collapse_to_one_site():
    """Three answers a few units apart are one recommendation printed three times.

    Deposits come in tight groups, so the top three by count are routinely near-identical
    coordinates - asked for ore and stone against the real dataset, the first two came
    back as (185, -475) and (188, -480).
    """
    nodes = [node(f"c{i}", "coal", i, 0, count=10 - i) for i in range(5)]
    kb = KnowledgeBase(game_version="test", lexicon=None, nodes=nodes,
                       base_radius=10.0)
    result = suggest_base_sites(kb, ["coal"], limit=3)
    assert len(result.sites) == 1


def test_position_breaks_a_tie_but_does_not_beat_coverage(kb):
    """`near` is injected from save state and orders equals; it must not promote a site
    that misses something just for being close."""
    result = suggest_base_sites(kb, ["coal", "ore"], near=(500, 0), limit=5)
    # Cluster B sits exactly on the reference point and has forty deposits, and it still
    # loses: it reaches no ore. Either end of the A pair is a correct top answer - both
    # cover both resources - and proximity picks the nearer, which is the tie-break doing
    # its job rather than overriding coverage.
    assert result.sites[0].complete
    assert result.sites[0].map_x != 500
    assert result.sites[0].distance is not None


def test_no_radius_raises_rather_than_inventing_one(kb):
    """A radius IS the question this class asks. The dispatcher checks first; this guard
    stops a future caller that forgets from getting a made-up circle."""
    kb.base_radius = None
    with pytest.raises(ValueError):
        suggest_base_sites(kb, ["coal"])


# ------------------------------------------------------------------ the card

def test_the_card_never_claims_the_ground_is_buildable(kb):
    """On EVERY card, not only the suspect ones - the card cannot tell which are suspect.

    Nothing found in the pak says whether ground is flat, underwater or inside a no-build
    zone, and a coordinate that reads as "build here" would be well-formed, in-bounds,
    correctly transformed and wrong. That is the failure shape this project keeps
    recording.
    """
    text = cards.base_site_card(suggest_base_sites(kb, ["coal"])).to_text()
    assert "flat or buildable" in text


def test_the_card_prints_the_radius_it_used(kb):
    """Every count on the card is "inside a circle this big", and a reader who does not
    know the size cannot judge the claim."""
    assert "10.0 map units" in cards.base_site_card(
        suggest_base_sites(kb, ["coal"])).to_text()


def test_the_card_is_amber_because_where_to_build_is_advice(kb):
    assert cards.base_site_card(
        suggest_base_sites(kb, ["coal"])).colour == cards.TIER_ADVICE


def test_the_card_leads_with_the_fact_that_nothing_reaches_everything():
    """The answer to the question actually asked. Buried under three partial sites it
    reads as though one of them covered everything."""
    nodes = [node("coal", "coal", 0, 0, count=5), node("ore", "ore", 500, 0, count=5)]
    kb = KnowledgeBase(game_version="test", lexicon=None, nodes=nodes, base_radius=10.0)
    result = suggest_base_sites(kb, ["coal", "ore"])
    assert result.complete_sites == 0
    assert "No single base reaches all of" in cards.base_site_card(result).to_text()


def test_a_single_resource_card_omits_the_coverage_tautology(kb):
    """With one resource named, every candidate covers it by construction - "327 of 327"
    is a tautology dressed as a statistic."""
    assert "reach all of it" not in cards.base_site_card(
        suggest_base_sites(kb, ["coal"])).to_text()


# ------------------------------------------------------------------ the real dataset

def test_the_built_radius_is_the_paks_value_through_the_shared_transform():
    """3500 world units over the fitted scale. If a patch moves either, this fails rather
    than quietly resizing every base site."""
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    path = root / "data" / "1.0.2" / "base_camp.json"
    if not path.exists():
        pytest.skip("base_camp.json not built")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["world_units"] == 3500.0
    assert raw["map_units"] == pytest.approx(raw["world_units"] / raw["scale"], abs=1e-3)


def test_the_real_bases_in_the_reference_save_contain_a_handful_of_clusters():
    """The corroboration the ingest records, kept as a test so a data refresh re-runs it.

    The reference save's three base camps are at (229,-487), (73,-399) and (285,625).
    Applying the radius contains small handfuls - not zero, which would say the radius is
    far too small, and not twenty, which would say it is far too large. Corroboration,
    not proof: nobody has measured the circle in game.
    """
    kb = KnowledgeBase.load("1.0.2")
    if kb.base_radius is None:
        pytest.skip("base_camp.json not built")
    for bx, by in ((228.9, -486.6), (72.5, -399.2)):
        inside = [n for n in kb.nodes
                  if math.dist((n.map_x, n.map_y), (bx, by)) <= kb.base_radius]
        assert 1 <= len(inside) <= 8, f"({bx},{by}) contains {len(inside)} clusters"
