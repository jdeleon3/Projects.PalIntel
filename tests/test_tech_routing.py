"""The Q6 fast-path branch, and the two guards that keep it honest.

The branch is safe for a structural reason and a measured one:

* **Structural** — it abstains whenever the utterance names an entity, so it cannot take
  a query any other class could answer. Same argument attribute search makes.
* **Measured** — the topic alone is not enough. Swept over the 271 A5 transcripts, the
  topic cue claimed *"can you explain technology points?"* and answered a request for an
  explanation with a shopping list. A recommendation frame is required as well, and the
  sweep then claims three prompts, all genuine, stealing nothing.

Most of these tests are therefore about the branch NOT firing.
"""
from __future__ import annotations

import pytest

from palintel.knowledge import KnowledgeBase
from palintel.routing import StubRouter
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, progression=True)


def call(router, text):
    return router.route(text, router._lexicon.rank(text), [])


def name(c):
    return c.name if isinstance(c, ToolCall) else None


# ------------------------------------------------------------------ what it claims

@pytest.mark.parametrize("text", [
    "what should I research next",
    "what should I unlock next",
    "what technology should I get next",
    "what can I unlock at level 30",
    "what should I spend my technology points on",
    "is it worth worrying about my next research",
])
def test_a_progression_question_is_claimed(router, text):
    assert name(call(router, text)) == "suggest_next_unlock"


def test_no_arguments_is_a_valid_call_here(router):
    """Unlike every other branch, this class needs nothing filled in: "what should I
    research next" is a complete question, so an empty call is correct rather than a
    model failing to fill a slot."""
    assert call(router, "what should I research next").args == {}


def test_a_goal_word_becomes_the_games_own_category(router):
    assert call(router, "what weapon should I research next").args["goal"] == "Weapon"
    assert call(router, "what should I research for my base").args["goal"] \
        == "BuildObject"


def test_a_level_is_read_as_the_players(router):
    """A LevelCap is a gate the game states, which is the case STATUS's 2026-08-11
    decision explicitly allows - the same amendment the mount work made."""
    args = call(router, "what can I unlock at level 30").args
    assert args["player_level"] == 30 and "level" not in args


def test_naming_the_ancient_pool_is_a_filter_and_not_dropped(router):
    """The first version of this branch answered "what should I spend my ancient
    technology points on" with ordinary-currency technologies - a filter the player
    stated, silently gone on the fast path, exactly like the mount work's dropped
    element."""
    args = call(router, "what should I spend my ancient technology points on").args
    assert args["currency"] == "ancient"


def test_an_ordinary_points_question_is_not_narrowed_to_one_pool(router):
    """"Technology points" is how people refer to the whole system, so reading it as a
    filter would narrow half the questions this branch exists for."""
    assert "currency" not in call(router, "what should I spend my technology "
                                          "points on").args


# ------------------------------------------------------------------ what it refuses

@pytest.mark.parametrize("text", [
    "can you explain technology points",
    "what changes with technology points",
    "how does the tech tree work",
])
def test_an_explanatory_question_is_not_claimed(router, text):
    """A wrong-class answer is worse than a decline because it looks like an answer -
    the first play session's finding, and these three would each get a shopping list."""
    assert name(call(router, text)) != "suggest_next_unlock"


def test_a_question_naming_a_pal_is_not_claimed(router):
    """The structural guard. "How do I unlock Anubis" is not a technology question, and
    the pattern cannot know what follows the verb - the candidate list can."""
    assert name(call(router, "how do I unlock Anubis")) != "suggest_next_unlock"


def test_a_question_naming_a_resource_is_not_claimed(router):
    assert name(call(router, "where can I research coal")) != "suggest_next_unlock"


def test_the_branch_is_off_when_it_was_not_switched_on(kb):
    """Off by default and passed in by `build_router`, like counters: a branch naming a
    tool whose dataset is absent produces a decline the player cannot act on."""
    off = StubRouter(kb.lexicon)
    assert name(call(off, "what should I research next")) != "suggest_next_unlock"


def test_a_tech_question_mentioning_a_job_does_not_become_an_attribute_search(router):
    """The one real collision. "What tech should I research for my mining pals" carries
    a job word AND the word "pal", so attribute search would claim it and answer a
    technology question with a roster. The tech branch runs first for that reason."""
    assert name(call(router, "what tech should I research for my mining pals")) \
        == "suggest_next_unlock"


# ------------------------------------------------------------------ the wiring

def test_build_router_turns_the_branch_on_for_both_stubs(kb):
    """**The test that exists because of an omission, not a design.**

    `StubRouter` grew the counter branch, the component scorer measured it at 16/16, and
    `build_router` never passed `counters=True` - so every counter question in play paid
    a model round trip for an answer the stub already had, for a day, while STATUS said
    the fast path had landed. A measurement of a component is not a measurement of the
    system.

    This asserts the flag reaches BOTH stubs, because the fast path and the backstop are
    separate instances and wiring one is exactly how the other stays dark.
    """
    from palintel.pipeline import _has_dataset, build_router

    if not _has_dataset(kb.game_version, "tech.json"):
        pytest.skip("tech.json not built")

    router = build_router(kb, prefer="stub")
    assert "+tech" in router.name

    # And through the wrappers, which is what production actually runs.
    from palintel.config import RouterConfig
    wrapped = build_router(kb, prefer="auto", router_config=RouterConfig())
    for stub in _stubs(wrapped):
        assert "+tech" in stub.name, f"{stub.name} did not get the tech branch"


def _stubs(router) -> list:
    """Every StubRouter reachable from a possibly-wrapped router."""
    found = []
    for attr in ("fast", "full", "primary", "backstop"):
        child = getattr(router, attr, None)
        if isinstance(child, StubRouter):
            found.append(child)
        elif child is not None and child is not router:
            found += _stubs(child)
    if isinstance(router, StubRouter):
        found.append(router)
    return found
