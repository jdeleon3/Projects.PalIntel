"""The Q5 fast-path branch, and the abstention that protects the tier boundary.

`where can I find Anubis` and `how do I beat Anubis` name the same entity, so the cue
carries the entire distinction between a Tier 1 fact card and a Tier 2 advice card.
These tests are mostly about the branch NOT firing.
"""
from __future__ import annotations

import pytest

from palintel.knowledge import KnowledgeBase
from palintel.routing import StubRouter
from palintel.tools import Decline


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, counters=True,
                      counterable={"anubis", "chillet", "grizzbolt"})


def call(router, text):
    return router.route(text, router._lexicon.rank(text), [])


def test_a_counter_question_is_claimed(router):
    c = call(router, "how do I beat Anubis")
    assert not isinstance(c, Decline)
    assert c.name == "plan_counters" and c.args["boss"] == "Anubis"


def test_a_location_question_is_not_claimed_as_a_counter(router):
    c = call(router, "where can I find Anubis")
    assert getattr(c, "name", None) == "find_pal_spawns"


def test_both_cue_families_present_abstains_to_the_model(router):
    """The tier is ambiguous, so the fast path declines to decide it. Abstaining costs
    one query's latency; claiming wrongly costs the tier."""
    c = call(router, "where can I find something to beat Anubis")
    assert getattr(c, "name", None) != "plan_counters"


def test_a_pal_with_no_boss_form_is_not_claimed(router):
    """Deferring rather than declining - the model may know it is a different question."""
    c = call(router, "how do I beat Lamball")
    assert getattr(c, "name", None) != "plan_counters"


def test_the_branch_is_off_by_default(kb):
    """Same shape as pal_spawns: a switch, so a regression can be attributed to
    registering the class rather than to cue width."""
    r = StubRouter(kb.lexicon, counterable={"anubis"})
    assert getattr(call(r, "how do I beat Anubis"), "name", None) != "plan_counters"


def test_a_drop_question_is_still_a_drop_question(router):
    c = call(router, "what does Chillet drop")
    assert getattr(c, "name", None) == "find_pal_drops"
