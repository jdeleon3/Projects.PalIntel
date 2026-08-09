"""Phase 1 slice tests.

Emphasis is on the invariants the design actually commits to, not just happy paths:
no fabricated coordinates, suspect clusters never served, declines stay honest.

    .venv\\Scripts\\python -m pytest tests -q
"""
from __future__ import annotations

import pytest

from palintel.cards import decline_card, resource_card
from palintel.execution import find_resource_nodes
from palintel.knowledge import KnowledgeBase, phonetic, squash
from palintel.pipeline import Pipeline, PlayerState
from palintel.routing import StubRouter
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def pipe(kb: KnowledgeBase) -> Pipeline:
    return Pipeline(kb, StubRouter(kb.lexicon, {n.resource for n in kb.nodes}))


# ----------------------------------------------------------------- knowledge base

def test_loads_expected_shape(kb: KnowledgeBase):
    assert kb.game_version == "1.0.2"
    assert len(kb.lexicon.pals()) > 300
    assert len(kb.nodes) > 2000


def test_suspect_clusters_are_never_loaded(kb: KnowledgeBase):
    """Origin-artifact clusters would send a player to a place that does not exist."""
    assert kb.excluded_suspect > 0, "fixture should exercise the exclusion"
    origin_x, origin_y = -344, 271
    for n in kb.nodes:
        assert n.distance_to(origin_x, origin_y) > 20, f"{n.node_id} near world origin"


def test_every_node_has_real_coordinates(kb: KnowledgeBase):
    for n in kb.nodes:
        assert isinstance(n.map_x, (int, float))
        assert n.node_count >= 1
        assert -2500 < n.map_x < 2500 and -2500 < n.map_y < 2500


# ------------------------------------------------------------------------ matching

@pytest.mark.parametrize("text, expect", [
    ("lee's bunk", "leesbunk"),
    ("My Korra", "mykorra"),
    ("health-sphere", "healthsphere"),
])
def test_squash_removes_tokenisation_artifacts(text, expect):
    assert squash(text) == expect


def test_phonetic_is_stable_across_spelling_noise():
    assert phonetic("Lifmunk") == phonetic("lifmunck")


def test_lexicon_ranks_without_filtering(kb: KnowledgeBase):
    """The corrector ranks; it must never decide. ADR-0016."""
    ranked = kb.lexicon.rank("where can I find health sphere", limit=5)
    assert ranked, "ranking must always return candidates"
    assert all(c.score > 0 for c in ranked)
    assert ranked == sorted(ranked, key=lambda c: -c.score)


def test_mangled_name_survives_into_the_candidate_set(kb: KnowledgeBase):
    """A verbatim transcript from the A5 evaluation: Helzephyr heard as "healthsphere".

    Asserts top-10, not top-1. Two earlier versions of this test were wrong in
    instructive ways:

    - top-5, from ADR-0016's original figures. Those came from an experiment that
      stripped query-template words, which did not help the correct entity so much as
      starve its competitors. Production cannot do that.
    - a hand-written "health sphere" (two words) instead of the recorded "healthsphere"
      (one). The extra token boundary spawns more competing n-grams and pushes the
      correct answer from rank 7 to rank 11.

    Both failures shared a cause: testing invented input rather than observed input.
    """
    heard = "Hey pal should I use healthsphere against the first tower"
    ranked = kb.lexicon.rank(heard, limit=10)
    assert "Helzephyr" in [c.canonical for c in ranked]


# ------------------------------------------------------------------------ execution

def test_returns_requested_resource_only(kb: KnowledgeBase):
    r = find_resource_nodes(kb, "coal", limit=5)
    assert r.nodes and all(n.resource == "coal" for n in r.nodes)


def test_sorts_by_distance_when_position_known(kb: KnowledgeBase):
    r = find_resource_nodes(kb, "coal", near=(0.0, 0.0), limit=5)
    dists = [n.distance_to(0.0, 0.0) for n in r.nodes]
    assert dists == sorted(dists)


def test_sorts_by_size_without_position(kb: KnowledgeBase):
    r = find_resource_nodes(kb, "coal", limit=5)
    counts = [n.node_count for n in r.nodes]
    assert counts == sorted(counts, reverse=True)


def test_unpopulated_level_data_does_not_silently_empty_results(kb: KnowledgeBase):
    """min_player_level is not yet derived; gating must not hide everything."""
    r = find_resource_nodes(kb, "coal", max_player_level=5, limit=3)
    assert r.nodes, "ungated nodes should still be returned"
    assert r.level_filtered is False


def test_limit_is_respected(kb: KnowledgeBase):
    assert len(find_resource_nodes(kb, "ore", limit=2).nodes) == 2


# -------------------------------------------------------------------------- routing

@pytest.mark.parametrize("utterance, resource", [
    ("where's the nearest coal", "coal"),
    ("hey pal find me an ore spot", "ore"),
    ("where's the closest sulfur deposit", "sulfur"),
    ("show me quartz near my base", "quartz"),
])
def test_routes_resource_queries(pipe: Pipeline, utterance, resource):
    out = pipe.handle(utterance)
    assert isinstance(out.call, ToolCall)
    assert out.call.args["resource"] == resource


@pytest.mark.parametrize("utterance", [
    "how do I breed Anubis",          # different query class, not yet registered
    "what should I research next",    # no location intent
])
def test_declines_rather_than_guessing(pipe: Pipeline, utterance):
    assert isinstance(pipe.handle(utterance).call, Decline)


def test_decline_offers_only_locatable_resources(pipe: Pipeline):
    """Crude oil is recognised but has no nodes, so must not be offered as findable."""
    out = pipe.handle("where's the nearest adamantium")
    assert isinstance(out.call, Decline)
    assert "crude_oil" not in out.call.known_options
    assert "coal" in out.call.known_options


def test_extracts_numeric_and_worded_levels(pipe: Pipeline):
    assert pipe.handle("find coal for level 25").call.args["max_player_level"] == 25
    assert pipe.handle("find coal for level twenty").call.args["max_player_level"] == 20


# ----------------------------------------------------------------------------- cards

def test_card_coordinates_come_from_the_node(kb: KnowledgeBase):
    """The invariant: every rendered coordinate traces to typed data, not a model."""
    r = find_resource_nodes(kb, "coal", limit=3)
    text = resource_card(r).to_text()
    for n in r.nodes:
        assert f"({n.map_x:.0f}, {n.map_y:.0f})" in text


def test_empty_result_never_renders_as_a_location(kb: KnowledgeBase):
    r = find_resource_nodes(kb, "coal", limit=0)
    card = resource_card(r)
    assert "No Coal found" in card.title


def test_crude_oil_explains_itself_rather_than_saying_none_nearby(kb: KnowledgeBase):
    r = find_resource_nodes(kb, "crude_oil", limit=3)
    assert not r.nodes
    assert "oil rigs" in resource_card(r).to_text()


def test_decline_card_names_unrecognised_token_when_known():
    card = decline_card(Decline(reason="test", unrecognized="adamantium"))
    assert "adamantium" in card.to_text()


def test_player_state_is_injected_not_parsed(pipe: Pipeline):
    """'nearest' must resolve against live state, not text."""
    far = pipe.handle("where's the nearest coal", PlayerState(base_coords=(800.0, 400.0)))
    near = pipe.handle("where's the nearest coal", PlayerState(base_coords=(20.0, -153.0)))
    assert far.card.lines[0] != near.card.lines[0]
