"""The free-text half of `item_source` — the model path's answer to the same question
the fast path answered with a second lexicon: 930 craftable items and a 151-item enum
that only covers the ones with drop data.

Same shape as `technology_lookup`'s `progression.find()`, deliberately: `items_named`
stays a validated enum for the items it already covers, and `item_name` is free text the
DISPATCHER resolves, matched whole-string rather than by the fast path's n-gram ranking -
which is the right choice specifically because a model's already-extracted phrase has no
sentence noise to strip, unlike a full spoken utterance.
"""
from __future__ import annotations

import pytest

from palintel.execution import find_item
from palintel.knowledge import KnowledgeBase
from palintel.pipeline import Pipeline, PlayerState
from palintel.routing_unified import unpack
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


# ------------------------------------------------------------------------- the matcher

def test_a_recipe_only_item_resolves(kb: KnowledgeBase):
    """The whole reason this exists: `Mega Sphere` has no drop data and is not in
    `items_named`'s enum, but it is one of the 930 recipes."""
    found = find_item("a mega sphere", kb)
    assert found and found[0] == "Mega Sphere"


def test_a_four_word_item_resolves_cleanly(kb: KnowledgeBase):
    """The exact case the fast path's own n-gram cap loses - `Lexicon.rank()` scores
    "High Quality Pal Oil" below two of its own siblings on a full sentence. A model's
    already-extracted phrase has no sentence noise around it, so whole-string matching
    does not have that problem."""
    found = find_item("the high quality pal oil", kb)
    assert found and found[0] == "High Quality Pal Oil"


def test_a_leading_article_is_stripped(kb: KnowledgeBase):
    assert find_item("cake", kb) == find_item("a cake", kb) == find_item("the cake", kb)


def test_something_that_is_not_an_item_matches_nothing(kb: KnowledgeBase):
    assert find_item("a nuclear reactor", kb) is None
    assert find_item("anubis", kb) is None


def test_real_schematic_tiers_defer_rather_than_pick_one(kb: KnowledgeBase):
    """94 real item groups in this dataset squash to one string once the tier digit is
    stripped - "Laser Rifle Schematic 2/3/4" all become "laserrifleschematic". The rule
    `progression.find` states for technology applies here for the same reason: a card
    cannot ask which tier you meant, so a tie is a decline, not a coin flip."""
    assert find_item("laser rifle schematic", kb) is None
    assert find_item("a katana schematic", kb) is None


# ---------------------------------------------------------------------- schema unpack

def test_item_name_is_carried_when_the_enum_is_empty():
    name, args = unpack("answer_query", {
        "query_class": "item_source", "pals": [], "resources": [],
        "items_named": [], "item_name": "Mega Sphere", "target": None,
        "max_player_level": None})
    assert name == "find_item_source"
    assert args == {"item_name": "Mega Sphere"}


def test_the_enum_wins_over_item_name_when_both_are_filled():
    """A validated choice beats an unvalidated one, whenever both are on offer - the
    opposite asymmetry from `plan_counters`' `target`, which wins over `pals` because
    THAT enum result is not guaranteed to be the boss. Here `items_named` IS guaranteed
    to be a real item; `item_name` is not."""
    name, args = unpack("answer_query", {
        "query_class": "item_source", "pals": [], "resources": [],
        "items_named": ["Flame Organ"], "item_name": "Mega Sphere", "target": None,
        "max_player_level": None})
    assert args == {"item": "Flame Organ"}
    assert "item_name" not in args


# --------------------------------------------------------------------------- dispatch

class _FixedRouter:
    name = "fixed"

    def __init__(self, call):
        self._call = call

    def route(self, utterance, candidates, context=None):
        return self._call


def test_item_name_resolves_to_a_real_card(kb: KnowledgeBase):
    p = Pipeline(kb, _FixedRouter(
        ToolCall("find_item_source", {"item_name": "a mega sphere"})))
    out = p.handle("what do I need for a mega sphere", PlayerState())
    assert not isinstance(out.call, Decline)
    assert "Mega Sphere" in out.card.title


def test_an_unresolvable_item_name_declines_with_the_raw_text_captured(kb: KnowledgeBase):
    """The point of the whole exercise: when the free-text resolver cannot place the
    item, the model's own attempt is captured as `Decline.unrecognized` rather than
    discarded - the first router in this project to ever populate that field with
    something real (see STATUS.md 1c)."""
    p = Pipeline(kb, _FixedRouter(
        ToolCall("find_item_source", {"item_name": "a floobtastic combobulator"})))
    out = p.handle("what do I need for a floobtastic combobulator", PlayerState())
    assert isinstance(out.call, Decline)
    assert out.call.unrecognized == "a floobtastic combobulator"


def test_neither_item_nor_item_name_declines_with_no_unrecognized(kb: KnowledgeBase):
    """A model that named the class and nothing else is a different failure from a
    model that tried and missed - `unrecognized` must stay None here, not fabricate a
    culprit from an empty call."""
    p = Pipeline(kb, _FixedRouter(ToolCall("find_item_source", {})))
    out = p.handle("what do I need for it", PlayerState())
    assert isinstance(out.call, Decline)
    assert out.call.unrecognized is None
