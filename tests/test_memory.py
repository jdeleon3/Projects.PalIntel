"""Conversation memory and follow-up resolution — ADR-0013.

The ADR is explicit that memory buys natural follow-ups at the price of a new failure
mode: a stale referent produces a card that looks entirely authoritative while answering
a question nobody asked. Most of these tests are about that price, not about the feature.
"""
from __future__ import annotations

import pytest

from palintel.cards import decline_card
from palintel.knowledge import KnowledgeBase
from palintel.memory import Memory, Turn
from palintel.pipeline import Pipeline
from palintel.routing import StubRouter
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture
def pipe(kb: KnowledgeBase) -> Pipeline:
    # Function-scoped: memory is state, and a module-scoped pipeline would leak one
    # test's referents into the next - the same bug these tests exist to prevent.
    return Pipeline(kb, StubRouter(kb.lexicon, {n.resource for n in kb.nodes}))


# --- the buffer --------------------------------------------------------------------

def test_turns_expire_by_ttl():
    m = Memory(ttl=60.0)
    m.remember(Turn(who="a", tool="find_resource_nodes",
                    entities={"resource": "coal"}, summary="", at=0.0))
    assert m.recent("a", now=30.0)
    assert not m.recent("a", now=120.0)


def test_the_buffer_is_bounded():
    m = Memory(max_turns=2)
    for r in ("coal", "ore", "quartz"):
        m.remember(Turn(who="a", tool="find_resource_nodes",
                        entities={"resource": r}, summary=""))
    assert [t.entities["resource"] for t in m.recent("a")] == ["ore", "quartz"]


def test_users_do_not_contaminate_each_other():
    """Per user, not per channel. Two people asking at once is the normal case."""
    m = Memory()
    m.remember(Turn(who="a", tool="find_resource_nodes",
                    entities={"resource": "coal"}, summary=""))
    assert m.recent("a") and not m.recent("b")


def test_expiry_is_distinguishable_from_never_having_spoken():
    """ADR-0013: expired context is not silently ignored. That needs the two cases to be
    tellable apart, because only one of them should ask for a restatement."""
    m = Memory(ttl=60.0)
    assert m.had_expired("a", now=0.0) is False
    m.remember(Turn(who="a", tool="find_resource_nodes",
                    entities={"resource": "coal"}, summary="", at=0.0))
    assert m.had_expired("a", now=30.0) is False
    assert m.had_expired("a", now=120.0) is True


def test_forget_is_scoped():
    m = Memory()
    for who in ("a", "b"):
        m.remember(Turn(who=who, tool="find_resource_nodes",
                        entities={"resource": "coal"}, summary=""))
    m.forget("a")
    assert not m.recent("a") and m.recent("b")


# --- follow-ups that should resolve --------------------------------------------------

def test_a_bare_referential_query_inherits_the_entity(pipe: Pipeline):
    pipe.handle("where can I find Chillet")
    out = pipe.handle("what about the alpha?")
    assert isinstance(out.call, ToolCall)
    assert out.call.name == "find_pal_spawns" and out.call.args["pal"] == "Chillet"


def test_a_follow_up_keeps_its_own_modifiers(pipe: Pipeline):
    """The entity comes from memory; `kind` comes from this utterance. The dispatcher
    reads modifiers off the text either way, so a follow-up gets them for free."""
    pipe.handle("where can I find Anubis")
    out = pipe.handle("what about the alpha?")
    assert "field alpha" in " ".join(out.card.lines).lower()


def test_a_follow_up_for_a_kind_that_does_not_exist_says_so(pipe: Pipeline):
    """Depresso has no alpha. "No Depresso spawns found" reads as missing data; the
    answer is that there is no such encounter."""
    pipe.handle("where can I find Depresso")
    out = pipe.handle("what about the alpha?")
    assert "has no field alpha" in out.card.title


def test_a_named_entity_in_a_follow_up_replaces_the_remembered_one(pipe: Pipeline):
    """"and coal?" after an ore query is a question about coal, not about both."""
    pipe.handle("where's the nearest ore")
    out = pipe.handle("and coal?")
    assert isinstance(out.call, ToolCall)
    assert out.call.args["resource"] == "coal"


def test_a_follow_up_switches_tools_when_it_names_the_other_kind(pipe: Pipeline):
    """"and coal?" after a Pal query is a resource question.

    Found by driving a real conversation: matching the REMEMBERED tool instead of the
    named subject answered it with the Pal again. A follow-up keeps the verb from the
    previous turn, never the entity, and the subject decides the tool.
    """
    pipe.handle("where can I find Anubis")
    out = pipe.handle("and coal?")
    assert isinstance(out.call, ToolCall)
    assert out.call.name == "find_resource_nodes"
    assert out.call.args["resource"] == "coal"


@pytest.mark.parametrize("utterance", [
    "how about breeding Anubis",
    "and is Chillet any good for logging",
])
def test_a_follow_up_opener_does_not_licence_a_new_verb(pipe: Pipeline, utterance: str):
    """"and coal?" is elliptical - it means "where is coal". "how about breeding Anubis"
    opens the same way but carries its own verb, and lending it the previous turn's verb
    answers a breeding question with a map location."""
    pipe.handle("where can I find Chillet")
    out = pipe.handle(utterance)
    assert isinstance(out.call, Decline), f"claimed: {out.call}"


def test_a_follow_up_inherits_the_tool_not_just_the_entity(pipe: Pipeline):
    """"and quartz?" has no location cue at all. It is a location question only because
    the previous turn was, which is exactly what storing the tool is for."""
    pipe.handle("where's the nearest ore")
    out = pipe.handle("and quartz?")
    assert isinstance(out.call, ToolCall)
    assert out.call.name == "find_resource_nodes"


def test_follow_ups_span_input_channels(pipe: Pipeline):
    """Ask by voice, follow up by text - ADR-0012's promise, and the whole reason
    `voice.speaker` exists.

    The microphone cannot say who spoke, so the voice path keys memory on the configured
    speaker name. Set it to the same Discord display name the text path uses and the two
    channels share one thread; leave it unset and they do not, which is the honest
    default because guessing would attribute speech to the wrong person.
    """
    pipe.handle("where can I find Chillet", who="jd")          # spoken
    out = pipe.handle("where's the closest one", who="jd")     # typed
    assert isinstance(out.call, ToolCall) and out.call.args["pal"] == "Chillet"


def test_unattributed_voice_keeps_its_own_thread(pipe: Pipeline):
    """The default. Not joining the two is a limitation; joining them wrongly is a bug."""
    pipe.handle("where can I find Chillet", who="voice")
    out = pipe.handle("where's the closest one", who="jd")
    assert isinstance(out.call, Decline) and out.call.needs_restatement


# --- follow-ups that must NOT resolve ------------------------------------------------

def test_a_fresh_question_ignores_memory(pipe: Pipeline):
    """The failure the ADR names. A question that names its own subject must never be
    answered against the previous one."""
    pipe.handle("where can I find Chillet")
    out = pipe.handle("where's the nearest coal")
    assert isinstance(out.call, ToolCall)
    assert out.call.name == "find_resource_nodes" and out.call.args["resource"] == "coal"


def test_expired_context_asks_for_restatement_rather_than_guessing(kb: KnowledgeBase):
    p = Pipeline(kb, StubRouter(kb.lexicon, {n.resource for n in kb.nodes}),
                 memory=Memory(ttl=0.0))
    p.handle("where can I find Chillet")
    out = p.handle("what about the alpha?")
    assert isinstance(out.call, Decline) and out.call.needs_restatement
    assert "forgotten" in " ".join(out.card.lines).lower()


def test_a_follow_up_with_no_history_at_all_asks_too(pipe: Pipeline):
    out = pipe.handle("what about the alpha?")
    assert isinstance(out.call, Decline) and out.call.needs_restatement


def test_a_decline_is_not_remembered(pipe: Pipeline):
    """A decline resolved nothing, so it offers no referent. Storing its best-guess
    candidate would manufacture one - the exact failure ADR-0013 warns about."""
    pipe.handle("where can I find Chillet")
    pipe.handle("what should I research next")        # declines
    out = pipe.handle("where's the closest one")
    assert isinstance(out.call, ToolCall) and out.call.args["pal"] == "Chillet"


def test_one_speakers_referent_never_answers_anothers_follow_up(pipe: Pipeline):
    pipe.handle("where can I find Chillet", who="alice")
    out = pipe.handle("what about the alpha?", who="bob")
    assert isinstance(out.call, Decline) and out.call.needs_restatement


def test_reset_forgets(pipe: Pipeline):
    pipe.handle("where can I find Chillet", who="jd")
    pipe.memory.forget("jd")
    out = pipe.handle("what about the alpha?", who="jd")
    assert isinstance(out.call, Decline) and out.call.needs_restatement


# --- presentation ---------------------------------------------------------------------

def test_the_restatement_card_asks_for_something_achievable():
    """Not "I didn't catch that" - we caught it perfectly and have nothing to refer to."""
    card = decline_card(Decline(reason="lost track", needs_restatement=True))
    assert "didn't catch" not in " ".join(card.lines).lower()
    assert "say the name again" in " ".join(card.lines).lower()


def test_only_resolved_entities_are_stored(pipe: Pipeline):
    """Not transcripts. Re-resolving "Lee's bunk" on every follow-up gives it a second
    chance to resolve differently, and one entity silently becomes two."""
    pipe.handle("where can I find Chillet", who="jd")
    turn = pipe.memory.last("jd")
    assert turn.entities == {"pal": "Chillet"}
    assert "where can I find" not in str(turn)
