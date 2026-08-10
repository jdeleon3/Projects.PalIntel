"""Phase 2 slice tests: find_pal_spawns, its card, and the two-class routing split.

The routing tests here pin decisions that were settled by measurement over the 240 A5
transcripts (see Docs/04-roadmap.md, Phase 2). They exist because the measurement is not
re-run on every commit and the constants it chose are otherwise indistinguishable from
guesses.
"""
from __future__ import annotations

import pytest

from palintel.cards import spawn_card
from palintel.execution import find_pal_spawns
from palintel.knowledge import KnowledgeBase
from palintel.pipeline import Pipeline, PlayerState, spawn_kind
from palintel.routing import PAL_CONFIDENT, StubRouter
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def pipe(kb: KnowledgeBase) -> Pipeline:
    return Pipeline(kb, StubRouter(kb.lexicon, {n.resource for n in kb.nodes}))


def stub(kb: KnowledgeBase, **kw) -> StubRouter:
    return StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, **kw)


# --- execution --------------------------------------------------------------------

def test_returns_only_the_requested_pal(kb: KnowledgeBase):
    r = find_pal_spawns(kb, "Chillet")
    assert r.areas and all(a.pal == "Chillet" for a in r.areas)


def test_sorts_by_distance_when_position_known(kb: KnowledgeBase):
    r = find_pal_spawns(kb, "Chillet", near=(0.0, 0.0), limit=5)
    d = [a.distance_to(0.0, 0.0) for a in r.areas]
    assert d == sorted(d)


def test_sorts_by_likelihood_not_raw_points(kb: KnowledgeBase):
    """Mimog sits at weight 2-4 in 139 sheets, so point count alone ranks filler first."""
    r = find_pal_spawns(kb, "Mimog", limit=5)
    density = [a.density for a in r.areas]
    assert density == sorted(density, reverse=True)


def test_a_pal_the_overworld_never_places_is_not_a_miss(kb: KnowledgeBase):
    """"Not out there" and "I have no data" are different claims. Only one is true."""
    r = find_pal_spawns(kb, "Bellanoir")
    assert r.in_overworld is False and not r.areas
    card = spawn_card(r)
    assert "isn't found in the overworld" in card.title


def test_alpha_only_pals_fall_through_and_say_so(kb: KnowledgeBase):
    """Necromus has no ordinary spawn. Returning nothing would be wrong; returning the
    alpha silently would send someone at a level 50 boss expecting a wild encounter."""
    r = find_pal_spawns(kb, "Necromus")
    assert r.areas and r.kind == "alpha" and r.kind_substituted
    assert "field alpha" in spawn_card(r).lines[0]


def test_explicit_kind_is_not_substituted(kb: KnowledgeBase):
    r = find_pal_spawns(kb, "Anubis", kind="alpha")
    assert r.areas and r.kind == "alpha" and r.kind_substituted is False
    assert all(a.kind == "alpha" for a in r.areas)


def test_normal_spawns_are_preferred_over_alphas_by_default(kb: KnowledgeBase):
    r = find_pal_spawns(kb, "Anubis")
    assert r.kind == "normal" and all(a.kind == "normal" for a in r.areas)


def test_limit_is_respected(kb: KnowledgeBase):
    assert len(find_pal_spawns(kb, "Chikipi", limit=2).areas) == 2


# --- the card ---------------------------------------------------------------------

def test_card_reports_encounter_share(kb: KnowledgeBase):
    """A coordinate without it reads as "go here and you'll find one", which for a 2%
    roll is how the player concludes the data is wrong."""
    r = find_pal_spawns(kb, "Mimog", limit=1)
    assert r.areas[0].encounter_share < 0.5
    assert "% of spawns here" in spawn_card(r).lines[0]

    # An area where the Pal is the only thing that spawns should not be cluttered with
    # "100% of spawns here".
    solo = find_pal_spawns(kb, "Anubis", kind="alpha", limit=1)
    assert "% of spawns here" not in spawn_card(solo).lines[0]


def test_card_never_invents_a_coordinate(kb: KnowledgeBase):
    """Every rendered coordinate must come from a real area in the knowledge base."""
    known = {(a.map_x, a.map_y) for a in kb.spawns}
    for pal in ("Chillet", "Anubis", "Lamball"):
        r = find_pal_spawns(kb, pal, limit=3)
        for a in r.areas:
            assert (a.map_x, a.map_y) in known


# --- routing: the two-class split -------------------------------------------------

def test_a_confident_pal_location_query_takes_the_fast_path(kb: KnowledgeBase):
    r = stub(kb)
    u = "hey pal, where can I find Suzaku?"
    call = r.route(u, kb.lexicon.rank(u))
    assert isinstance(call, ToolCall) and call.name == "find_pal_spawns"
    assert call.args["pal"] == "Suzaku"


def test_a_mangled_pal_name_defers_instead_of_guessing(kb: KnowledgeBase):
    """The measured reason PAL_CONFIDENT is 0.85 and not 0.78.

    At 0.78 this answered with Rayhound Cryst. The ranker has no sentence context; the
    model does, and recovers these (ADR-0016). A spawn card naming the wrong species is
    the failure ADR-0007 refuses to ship.
    """
    u = "Hey pal, where can I find Banner and Cryst?"
    cands = kb.lexicon.rank(u)
    assert isinstance(stub(kb).route(u, cands), Decline)
    # ...and it is the floor doing it, not the cues or the tool being unregistered.
    loose = stub(kb, pal_floor=0.78).route(u, cands)
    assert isinstance(loose, ToolCall) and loose.args["pal"] != "Vanwyrm Cryst"


def test_pal_floor_is_above_the_resource_floor(kb: KnowledgeBase):
    """313 Pals against 4 resources: the top candidate is a much weaker signal."""
    assert PAL_CONFIDENT > 0.78


@pytest.mark.parametrize("utterance", [
    "hey pal, is Pierdon any good for logging?",
    "hey pal, do I need a better spear for Mereth?",
    "hey pal, is Prickster any good against the first tower?",
])
def test_wide_cues_never_reach_the_pal_branch(kb: KnowledgeBase, utterance: str):
    """`wide`'s intent guesses were each earned by a real RESOURCE query.

    Applied to a Pal name they fire on questions that are not about location at all -
    nine of them on the A5 set - so the Pal branch is gated on the narrower cue set
    whatever the resource branch is configured to.
    """
    call = stub(kb, cues="wide").route(utterance, kb.lexicon.rank(utterance))
    assert isinstance(call, Decline), f"claimed: {call}"


def test_wide_keeps_its_resource_coverage_after_the_split(kb: KnowledgeBase):
    """The split must not cost Q1 anything - that is the whole reason to prefer it over
    stepping back to `proximity`."""
    u = "hey pal, what's the best place to farm quartz"
    call = stub(kb, cues="wide").route(u, kb.lexicon.rank(u))
    assert isinstance(call, ToolCall) and call.name == "find_resource_nodes"


# --- dispatch ---------------------------------------------------------------------

@pytest.mark.parametrize("utterance, expected", [
    ("where's the alpha Anubis", "alpha"),
    ("where is the Anubis lord", "alpha"),
    ("where can I find a predator Chillet", "predator"),
    ("where can I find Anubis", None),
    # "boss" is deliberately unmatched: players call tower bosses, raid bosses and field
    # alphas all "boss", and only the last is in this dataset.
    ("where's the Anubis boss", None),
])
def test_encounter_kind_is_read_off_the_utterance(utterance: str, expected):
    assert spawn_kind(utterance) == expected


def test_dispatch_renders_both_members_of_a_variant_family(pipe: Pipeline,
                                                           kb: KnowledgeBase):
    """Menasting and Menasting Terra share a Paldeck slot and spawn in different places,
    so a query that cannot be narrowed to one has two correct answers, not one."""
    family = kb.lexicon.family("Menasting")
    assert len(family) == 2
    out = pipe.handle("hey pal, where can I find Menasting?")
    assert out.call.name == "find_pal_spawns"
    assert len(out.cards) == 2
    assert {c.title.split(" locations")[0] for c in out.cards} == set(family)


def test_dispatch_injects_player_position(pipe: Pipeline, kb: KnowledgeBase):
    """"Nearest" has to resolve against where the player actually is."""
    here = (287.0, 623.0)
    out = pipe.handle("where's the nearest Chikipi",
                      PlayerState(player_coords=here))
    nearest = find_pal_spawns(kb, "Chikipi", near=here, limit=1).areas[0]
    assert f"({nearest.map_x:.0f}, {nearest.map_y:.0f})" in out.card.lines[0]


def test_a_pal_call_with_no_pal_declines_rather_than_raising(kb: KnowledgeBase):
    """A model can name a registered tool and omit its only required argument."""
    class _Fixed:
        name = "fixed"

        def route(self, utterance, candidates, context=None):
            return ToolCall(name="find_pal_spawns", args={})

    out = Pipeline(kb, _Fixed()).handle("where can I find something")
    assert isinstance(out.call, Decline)
