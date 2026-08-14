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


def test_both_cue_families_present_answers_both(router):
    """Not ambiguity to resolve - two questions. Picking one is a coin flip on the
    tier, so the spawn call is chained behind the counter call."""
    c = call(router, "where can I find something to beat Anubis")
    assert c.name == "plan_counters" and c.args["boss"] == "Anubis"
    assert c.then is not None
    assert c.then.name == "find_pal_spawns" and c.then.args["pal"] == "Anubis"


def test_a_pure_counter_question_chains_nothing(router):
    """The second card is for questions that asked two things, not a default."""
    assert call(router, "how do I beat Anubis").then is None


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


# --- chained dispatch --------------------------------------------------------

from palintel.knowledge import KnowledgeBase as _KB  # noqa: E402
from palintel.pipeline import MAX_CARDS, Outcome, Pipeline  # noqa: E402
from palintel.tools import Decline, ToolCall  # noqa: E402


class _FixedRouter:
    name = "fixed"

    def __init__(self, call):
        self._call = call

    def route(self, utterance, candidates, context=None):
        return self._call


@pytest.fixture(scope="module")
def pipe(kb):
    return Pipeline(kb, _FixedRouter(None))


def _run(kb, call, who="t"):
    return Pipeline(kb, _FixedRouter(call)).handle("where can I find Lamball", who=who)


def test_a_chained_call_renders_both_answers(kb):
    out = _run(kb, ToolCall("find_pal_drops", {"pal": "Lamball"},
                            then=ToolCall("find_pal_spawns", {"pal": "Lamball"})))
    assert len(out.cards) == 2
    assert out.cards[0].title != out.cards[1].title


def test_the_chain_does_not_exceed_the_card_cap(kb):
    """Past MAX_CARDS a second answer stops being a second opinion and becomes a wall.
    The primary wins, because it is the branch the cue led with."""
    out = _run(kb, ToolCall("find_pal_spawns", {"pal": "Chillet"},
                            then=ToolCall("find_pal_drops", {"pal": "Chillet"})))
    assert len(out.cards) <= MAX_CARDS


def test_a_declining_second_call_is_dropped_not_shown(kb):
    """A decline card beside a good answer reads as though part of the question failed,
    when the part worth answering was answered."""
    out = _run(kb, ToolCall("find_pal_drops", {"pal": "Lamball"},
                            then=ToolCall("find_pal_spawns", {})))
    assert len(out.cards) == 1
    assert not isinstance(out.call, Decline)


def test_memory_records_only_the_primary_call(kb):
    """One referent per turn. Storing both leaves "what about the alpha?" resolving
    against whichever ran last rather than what the player led with."""
    p = Pipeline(kb, _FixedRouter(
        ToolCall("find_pal_drops", {"pal": "Lamball"},
                 then=ToolCall("find_pal_spawns", {"pal": "Lamball"}))))
    p.handle("what does Lamball drop", who="u")
    turns = p.memory.recent("u")
    assert [t.tool for t in turns] == ["find_pal_drops"]


def test_the_attacker_position_is_not_claimed(router):
    """"Is Prixter any good against the first tower" names the Pal you would BRING,
    against a boss it never names. Measured on the A5 transcripts: this phrasing was
    claimed three times and would have produced a plan for fighting Prixter."""
    for text in ("is Prixter any good against the first tower",
                 "is Anubis any good against the first tower",
                 "is Anubis strong against the tower boss"):
        assert getattr(call(router, text), "name", None) != "plan_counters", text


# --- dispatch ----------------------------------------------------------------

from palintel.pipeline import PlayerState  # noqa: E402


def test_a_counter_question_renders_a_card_end_to_end(kb):
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", {"boss": "Anubis"})))
    out = p.handle("how do I beat Anubis",
                   PlayerState(owned_species=frozenset({"lifmunk", "cutefox"})))
    assert not isinstance(out.call, Decline)
    assert "Anubis" in out.card.title


def test_an_unread_roster_does_not_claim_you_own_nothing(kb):
    """"You own nothing that works" and "I never looked" are different answers.
    Reading the roster costs a full Level.sav parse, so absent is the normal case."""
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", {"boss": "Anubis"})))
    text = p.handle("how do I beat Anubis", PlayerState()).card.to_text()
    assert "Nothing you own" not in text
    assert "haven't read your Pals" in text


def test_an_empty_roster_does_say_you_own_nothing(kb):
    """Read and empty is a real answer, and a different one."""
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", {"boss": "Anubis"})))
    text = p.handle("how do I beat Anubis",
                    PlayerState(owned_species=frozenset())).card.to_text()
    assert "Nothing you own" in text


def test_an_unknown_boss_declines_rather_than_raising(kb):
    """Note Lamball is NOT this case: 364 field alphas mean nearly every Pal has a boss
    form, so `how do I beat Lamball` is a real question with a real answer."""
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", {"boss": "Flamewyrm"})))
    assert isinstance(p.handle("how do I beat it", PlayerState()).call, Decline)


def test_an_ordinary_pal_is_counterable_because_it_has_an_alpha(kb):
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", {"boss": "Lamball"})))
    out = p.handle("how do I beat Lamball", PlayerState())
    assert not isinstance(out.call, Decline)
    assert "field alpha" in out.card.to_text()


def test_a_missing_boss_argument_declines(kb):
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", {})))
    assert isinstance(p.handle("how do I beat it", PlayerState()).call, Decline)


# --- the model path ----------------------------------------------------------

from palintel.routing_unified import (CLASS_TO_TOOL, PRODUCTION_CLASSES,  # noqa: E402
                                      unpack)


def test_the_model_can_choose_the_counter_class():
    """Until this shipped, only phrasings the regex caught were answered at all."""
    assert "boss_counter" in PRODUCTION_CLASSES
    assert CLASS_TO_TOOL["boss_counter"] == "plan_counters"


def test_boss_counter_unpacks_the_pal_into_the_boss_slot():
    """With no target, the boss arrives resolved through the `pals` enum."""
    name, args = unpack("answer_query", {"query_class": "boss_counter",
                                         "pals": ["Anubis"], "resources": [],
                                         "items_named": [], "target": None})
    assert name == "plan_counters"
    assert args["boss"] == "Anubis"


def test_boss_counter_prefers_target_over_a_named_pal():
    """G4, 2026-08-14: "is Prixter any good against the first tower" is pals=["Prixter"]
    AND target="the first tower" - Prixter is the Pal you would BRING, not the boss.
    `unpack` used to let the positional `pals` zip win regardless, producing a confident
    plan for fighting Prixter. `target` must win whenever it is filled, not only when
    `pals` came back empty - this is the exact payload that reproduced the bug."""
    name, args = unpack("answer_query", {"query_class": "boss_counter",
                                         "pals": ["Prixter"], "resources": [],
                                         "items_named": [], "target": "the first tower"})
    assert name == "plan_counters"
    assert args["boss"] == "the first tower"
    assert args["boss"] != "Prixter"


def test_g4_attacker_framing_declines_end_to_end(kb):
    """The full round trip: an "against the first tower" call, unpacked, dispatched. It
    must decline - not answer about Prixter, and not raise - because "the first tower"
    names no boss `counters.plan` has a row for. A confident card here is the failure
    Block G tests for; a decline is the pass."""
    _, args = unpack("answer_query", {"query_class": "boss_counter", "pals": ["Prixter"],
                                      "resources": [], "items_named": [],
                                      "target": "the first tower"})
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", args)))
    out = p.handle("is Prixter any good against the first tower", PlayerState())
    assert isinstance(out.call, Decline)
    assert "Prixter" not in out.call.reason


def test_a_counter_class_naming_no_pal_yields_no_boss_and_declines(kb):
    """The model can name a class and omit its argument - observed on Gemini for the
    resource tool. An honest decline, not a TypeError."""
    _, args = unpack("answer_query", {"query_class": "boss_counter", "pals": [],
                                      "resources": [], "items_named": [],
                                      "target": None})
    assert not args.get("boss")
    p = Pipeline(kb, _FixedRouter(ToolCall("plan_counters", args)))
    assert isinstance(p.handle("how do I beat it", PlayerState()).call, Decline)
