"""The Q4 fast-path branch, whose safety rests on one word.

Every other branch added in Phase 4 abstains structurally: attribute search and
progression both refuse whenever the utterance names an entity. This one *needs* the
entity - a base is built for something - so nothing structural separates it from an
ordinary location question, and the cue carries the whole distinction:

    "where's the coal near my base"          -> a location question
    "where should I build my base for coal"  -> a siting question

Both name `coal` and both say `base`. Only the second has a placement verb, and that is
the rule `_BASE_CUE` encodes. Most of these tests are the first sentence, not the second.

Swept over the 271 A5 transcripts the branch changes **nothing** - the corpus predates
the class and holds no base questions - so its precision is confirmed against real speech
and its recall is entirely untested. That is worth knowing before reading these as
coverage.
"""
from __future__ import annotations

import pytest

from palintel.knowledge import KnowledgeBase
from palintel.routing import StubRouter
from palintel.tools import ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}),
                      cues="wide", base_sites=True)


def call(router, text):
    return router.route(text, router._lexicon.rank(text), [])


def name(c):
    return c.name if isinstance(c, ToolCall) else None


# ------------------------------------------------------------------ what it claims

@pytest.mark.parametrize("text", [
    "where should I build my base for coal",
    "where should I put a base for coal",
    "best base spot for coal",
    "good place for a base with coal",
    "where do I set up a base for coal",
])
def test_a_placement_question_is_claimed(router, text):
    c = call(router, text)
    assert name(c) == "suggest_base_sites"
    assert c.args["resources"] == ["coal"]


def test_several_resources_are_all_carried(router):
    """The reason the class exists: one circle reaching two things, which no other tool
    here can express."""
    c = call(router, "where should I build a base for ore and coal")
    assert set(c.args["resources"]) == {"ore", "coal"}


# ------------------------------------------------------------------ what it refuses

def test_a_location_question_mentioning_a_base_is_not_claimed(router):
    """The one that matters. `base` appears and there is no placement verb, so this is
    "where is the coal", answered by the resource branch."""
    assert name(call(router, "where's the coal near my base")) == "find_resource_nodes"


@pytest.mark.parametrize("text", [
    "what's at my base",
    "how many pals work at my base",
    "where's the nearest coal",
])
def test_no_placement_verb_means_no_claim(router, text):
    assert name(call(router, text)) != "suggest_base_sites"


def test_a_placement_question_naming_nothing_locatable_is_not_claimed(router):
    """No resource means no base site: a base is built FOR something, and a card of the
    biggest clusters on the map is not an answer to "where should I build"."""
    assert name(call(router, "where should I build my base")) != "suggest_base_sites"


def test_naming_a_resource_with_no_nodes_defers_rather_than_dropping_it(router):
    """Crude oil is in the lexicon and has no placed nodes. Answering about the rest of
    the sentence would silently drop a filter the player stated - the failure the mount
    work found in "which dragons can I ride at level 60"."""
    assert name(call(router, "where should I build a base for crude oil")) \
        != "suggest_base_sites"


def test_the_branch_is_off_when_it_was_not_switched_on(kb):
    """base_camp.json carries the radius, and a radius is the whole question."""
    off = StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}), cues="wide")
    assert name(call(off, "where should I build my base for coal")) \
        != "suggest_base_sites"


# ------------------------------------------------------------------ rating a place

@pytest.mark.parametrize("text,own", [
    ("how good is my base location", True),
    ("rate my base", True),
    ("how good is this base spot", False),
    ("rate this base location", False),
    ("is this a good spot for a base", False),
    ("what do you think of this location", False),
])
def test_a_rating_question_is_claimed_and_resolves_the_right_place(router, text, own):
    """Two readings of one class, and they point at different ground. "My base" is where
    they built; anything else is where they stand. A player asking about their base while
    standing somewhere else would otherwise be rated on the wrong spot and never know."""
    c = call(router, text)
    assert name(c) == "rate_base_site"
    assert bool(c.args.get("own_base")) is own


def test_a_siting_question_is_not_claimed_as_a_rating(router):
    """The two are told apart by what they want, not by a shared word: siting names a
    resource and asks WHERE, rating names nothing and asks HOW GOOD."""
    assert name(call(router, "where should I build my base for coal")) \
        == "suggest_base_sites"


def test_how_good_is_a_pal_is_not_a_base_rating(router):
    """The no-entity guard. "How good is Anubis" carries the rating cue and is an info
    question about a Pal."""
    assert name(call(router, "how good is Anubis")) != "rate_base_site"


# ------------------------------------------------------------------ the wiring

def test_build_router_turns_the_branch_on_for_both_stubs(kb):
    """Same test as the tech branch has, for the same recorded omission: the counter fast
    path shipped dark for a day because `build_router` was never given the flag."""
    from palintel.config import RouterConfig
    from palintel.pipeline import build_router
    from tests.test_tech_routing import _stubs

    if kb.base_radius is None:
        pytest.skip("base_camp.json not built")
    for stub in _stubs(build_router(kb, prefer="auto", router_config=RouterConfig())):
        assert "+bases" in stub.name, f"{stub.name} did not get the base branch"
