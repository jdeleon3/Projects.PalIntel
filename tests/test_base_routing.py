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


def test_naming_a_resource_with_no_nodes_defers_rather_than_dropping_it(kb):
    """A resource the lexicon knows and the siting maths cannot place must defer.
    Answering about the rest of the sentence would silently drop a filter the player
    stated - the failure the mount work found in "which dragons can I ride at level 60".

    **Built from a router that omits coal, rather than by naming crude oil.** Crude oil
    was the live example until 2026-08-12, when it turned out to have 185 oil fields and
    the premise evaporated; today no resource in the lexicon is unplaced. The guard is
    still right - `_resources` and `_locatable` can diverge again, and some future
    material will be craft-only - so it is tested on the divergence itself rather than on
    whichever resource happened to be missing that week.
    """
    blind = StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes} - {"coal"}),
                       cues="wide", base_sites=True)
    assert name(call(blind, "where should I build a base for coal")) \
        != "suggest_base_sites"
    # The same router still answers about something it CAN place, or the assertion above
    # would pass for the wrong reason.
    assert name(call(blind, "where should I build a base for quartz")) \
        == "suggest_base_sites"


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


# ------------------------------------------------------------------ stated coordinates

@pytest.mark.parametrize("text,want", [
    ("rate the base location at 185, -475", (185.0, -475.0)),
    ("how good is (321, 500) for a base", (321.0, 500.0)),
    ("how good is [229 -487]", (229.0, -487.0)),
    ("rate this spot 185 -475", (185.0, -475.0)),
    ("how good is -53, -960 for a base", (-53.0, -960.0)),
    ("rate coords 292 243", (292.0, 243.0)),
])
def test_a_stated_coordinate_is_read(text, want):
    """Bracketed, announced, or containing a negative. Three forms, because a bare pair
    of positive numbers is not distinctive enough to act on."""
    from palintel.routing import coordinates

    assert coordinates(text) == want


@pytest.mark.parametrize("text", [
    "rate this base at level 60",
    "how good is this base with 3 pals and 20 stone",
    "rate this base at level 20, 30 stone",
    "how good is my base, 4 of 10",
    "what should I research at level 30",
    "where should I build my base for coal",
])
def test_numbers_that_are_not_a_position_are_not_read_as_one(text):
    """**The asymmetry that sets the strictness.** Missing a coordinate costs a
    restatement; reading a level and a Pal count as a position produces a confident card
    about somewhere nobody mentioned.

    "rate this base at level 20, 30 stone" is the one that broke the first version, which
    accepted any comma anywhere in the sentence and read (20, 30).
    """
    from palintel.routing import coordinates

    assert coordinates(text) is None


def test_a_coordinate_is_enough_of_a_subject_on_its_own(router):
    """"How good is (185, -475)" names no base and no spot. Demanding the noun as well
    would decline the most precise way there is to point at a place - and it is exactly
    what a follow-up to a `suggest_base_sites` card looks like, since that card answers
    in coordinates."""
    assert name(call(router, "how good is (185, -475)")) == "rate_base_site"


def test_a_stated_coordinate_beats_my_base(router):
    """Both readings are present in "rate my base at 185, -475" and only one of them was
    said out loud with a number attached."""
    c = call(router, "how good is my base at 185, -475")
    assert name(c) == "rate_base_site"
    assert not c.args.get("own_base")


# --------------------------------------------------------- a rating with a resource

def test_a_named_resource_becomes_the_filter_rather_than_a_reason_to_abstain(router):
    """**The one place a named entity does not make this branch stand down.**

    Every other no-entity branch abstains the moment something resolves, because a named
    entity means another class owns the question. Here a named RESOURCE is the question:
    "is this a good spot for a quartz base" is the rating with a subject.
    """
    c = call(router, "is this a good spot for a quartz base")
    assert name(c) == "rate_base_site"
    assert c.args["resources"] == ["quartz"]


def test_a_named_pal_still_makes_it_abstain(router):
    """A Pal is not a filter. "How good is Anubis" is an info question and always was."""
    assert name(call(router, "how good is Anubis")) != "rate_base_site"


def test_a_resource_and_a_coordinate_together(router):
    c = call(router, "how good is (321, 500) for a coal base")
    assert name(c) == "rate_base_site" and c.args["resources"] == ["coal"]


def test_a_resource_with_no_placed_nodes_defers(kb):
    """The rating branch's half of the guard above, built the same way and for the same
    reason - see that test for why this no longer names crude oil."""
    blind = StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes} - {"coal"}),
                       cues="wide", base_sites=True)
    assert name(call(blind, "is this a good spot for a coal base")) != "rate_base_site"
    assert name(call(blind, "is this a good spot for a quartz base")) == "rate_base_site"


def test_a_siting_question_naming_a_resource_is_still_siting(router):
    """The two are told apart by the verb, and adding a resource to the rating branch
    must not have blurred that."""
    assert name(call(router, "where should I build my base for quartz")) \
        == "suggest_base_sites"


# ------------------------------------------------- the general question, about no place

@pytest.mark.parametrize("text", [
    "what makes a good base",
    "what makes a good base location",
    "what should I look for in a base location",
    "how do I choose a base location",
    "what do you check for a base spot",
])
def test_the_general_question_is_claimed(router, text):
    c = call(router, text)
    assert name(c) == "explain_base_criteria" and c.args == {}


def test_the_three_base_shapes_do_not_collide(router):
    """Siting needs a placement verb and a resource, rating needs "how good" and a
    subject, and criteria needs neither verb. Three questions about bases, three answers,
    and none of them is the others."""
    assert name(call(router, "where should I build my base for coal")) \
        == "suggest_base_sites"
    assert name(call(router, "how good is my base location")) == "rate_base_site"
    assert name(call(router, "what makes a good base")) == "explain_base_criteria"


def test_the_corpus_does_not_get_the_general_question(kb):
    """**The wrong-class answer this branch exists to prevent.**

    The game's own *Base* help entry explains the Palbox - summoning Pals, guild
    territory, base missions - and says nothing about choosing where to put one. It
    scores well on the words, so without this branch above it the corpus answers "what
    makes a good base" with a passage that reads entirely correct and addresses a
    different question.
    """
    from palintel.pipeline import _corpus_probe

    probe = _corpus_probe("1.0.2")
    if probe is None or kb.base_features is None:
        pytest.skip("corpus.json or base_features.json not built")
    both = StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}), cues="wide",
                      base_sites=True, corpus=probe)
    assert name(call(both, "what makes a good base")) == "explain_base_criteria"


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
