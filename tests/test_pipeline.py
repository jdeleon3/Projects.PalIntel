"""Phase 1 slice tests.

Emphasis is on the invariants the design actually commits to, not just happy paths:
no fabricated coordinates, suspect clusters never served, declines stay honest.

    .venv\\Scripts\\python -m pytest tests -q
"""
from __future__ import annotations

import math
import threading
from dataclasses import replace

import pytest

from palintel.cards import decline_card, resource_card
from palintel.execution import find_resource_nodes
from palintel.knowledge import KnowledgeBase, phonetic, squash
from palintel.pipeline import Pipeline, PlayerState
from palintel.routing import (BACKSTOP_CONFIDENT, FallbackRouter, FastPathRouter,
                              StubRouter)
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


def test_no_cluster_is_implausibly_dense(kb: KnowledgeBase):
    """Density is the signature of coordinates collapsing to a point.

    A cluster spans at most ~110 m by construction. Before the extractor resolved
    parent transforms, scattered nodes stored relative to a placement volume collapsed
    onto world origin and produced a 171-deposit phantom "hotspot" at (-344, 271) -
    a real-looking map position with nothing there. The builder now fails closed on
    this; asserting it at load time too keeps a bad dataset from being served.

    Density alone stopped being the test when the resource set widened past rock. Berries
    grow in real thickets - the largest is 61 bushes at 61 distinct coordinates, median
    7.8 map units out - so a bare count rejected genuine patches while calling them
    collapsed coordinates. Collapse is density with NO spread; both have to hold.
    """
    for n in kb.nodes:
        assert not (n.node_count > 50 and n.spread < 2.0), (
            f"{n.node_id} has {n.node_count} deposits at spread {n.spread}")


def test_only_overworld_coal_is_published(kb: KnowledgeBase):
    """497 of the 998 extracted coal spawners are overworld; the rest are cave coal.

    This test used to assert 998 - every extracted spawner - on the reasoning that a
    stopgap origin filter had once cost 152 real deposits, so none should be dropped.
    The premise was wrong in a way nothing text-only could show: the 672 missing ones
    are in `L15_X0_Y0`, the World Partition cell holding dungeon interiors, and their
    coordinates are not overworld positions at all. Published, they put coal markers in
    open water. See tests/test_node_scope.py.

    The number moved twice. It was 998 - every extracted spawner - until 672 turned out
    to be in `L15_X0_Y0`, the cell holding dungeon contents. Then that filter proved too
    blunt: 633 actors in that cell carry an Owner, and composing it recovers a real world
    position. Excluding them cost 171 coal deposits and was caught by standing on one.

    So the Phase 1 placement-volume fix was not moot after all. Every owner-chain
    resolution is in that cell, and those are exactly the actors worth keeping.
    """
    coal = sum(n.node_count for n in kb.nodes if n.resource == "coal")
    assert coal == 497, f"expected the 497 overworld coal deposits, got {coal}"


def test_no_node_sits_at_the_world_origin(kb: KnowledgeBase):
    """The origin artifact, guarded directly rather than via a deposit count.

    Actors stored relative to a parent collapse onto world origin, which maps to a
    plausible-looking spot with nothing there - the failure that produced a phantom
    171-deposit coal hotspot. Nothing published should land there.
    """
    at_origin = [n for n in kb.nodes if math.hypot(n.map_x, n.map_y) < 5]
    assert not at_origin, f"{len(at_origin)} nodes at map origin: {at_origin[:3]}"


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


def test_ungated_nodes_survive_a_level_filter(kb: KnowledgeBase):
    """A node with no derived level must not be hidden by gating.

    `min_player_level` is derived now, but not for every node: 17 clusters have no wild
    spawn area within the local radius and carry None. Dropping those would quietly
    shrink the answer set for a reason the player cannot see, so they pass the filter and
    the result reports that gating was only partial.

    Quartz rather than coal: once cave coal was excluded, every remaining coal cluster
    has a derived level and the lowest is 15, so coal can no longer exercise this at all.
    Quartz at level 5 returns exactly its two ungated clusters, which is the case itself.
    """
    ungated = [n for n in kb.nodes if n.min_player_level is None]
    assert ungated, "expected some nodes with no local spawn data"

    r = find_resource_nodes(kb, "quartz", max_player_level=5, limit=3)
    assert r.nodes, "ungated nodes should still be returned"
    assert r.level_filtered is True, "quartz has derived levels, so gating applied"
    assert any(n.min_player_level is None or n.min_player_level <= 5 for n in r.nodes)


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
    """Only resources with nodes may be offered as findable.

    Asserted against the locatable set rather than by naming an excluded resource: the one
    that used to be excluded - crude oil - turned out to have 185 placements, so a test
    written around that name was testing a data accident.
    """
    out = pipe.handle("where's the nearest adamantium")
    assert isinstance(out.call, Decline)
    placed = {n.resource for n in pipe.kb.nodes}
    assert set(out.call.known_options) <= placed
    assert "coal" in out.call.known_options


def test_compound_noun_prefers_the_specific_resource(pipe: Pipeline):
    """"Quartz ore" means quartz. Both match at 1.00, so the tie-break decides.

    Before specificity broke the tie, the winner was whichever entity the lexicon
    loaded first - this query answered with ore.
    """
    out = pipe.handle("hey pal where can I find quartz ore")
    assert out.call.args["resource"] == "quartz"


def test_plain_generic_resource_still_wins_alone(pipe: Pipeline):
    """Specificity must not stop a bare 'ore' query resolving to ore."""
    assert pipe.handle("find me an ore spot").call.args["resource"] == "ore"


def test_pal_question_never_grabs_a_weak_resource(pipe: Pipeline):
    """"Where can I find Suzaku" answered with a coal location.

    Suzaku ranked first at 1.00, but the stub skipped it (Pal, not resource) and took
    the first resource candidate at any score - coal, matched at 0.57 against the word
    "pal" in the wake phrase. ADR-0016 moved the confidence judgement to the router;
    the stub has no context to judge with, so it needs its own floor.

    Phase 2 changes what follows the guard, not the guard: with `find_pal_spawns`
    registered a confident Pal is answered rather than declined. What must never happen
    - then or now - is the query landing on the resource tool.
    """
    out = pipe.handle("Hey Pal, where can I find Suzaku?")
    assert isinstance(out.call, ToolCall)
    assert out.call.name == "find_pal_spawns"
    assert out.call.args["pal"] == "Suzaku"


def test_pal_guard_still_declines_when_no_pal_tool_is_registered(kb: KnowledgeBase):
    """The Phase 1 router, reachable via `pal_spawns=False`, is unchanged.

    Keeping it constructible is what lets a Phase 2 regression be attributed to
    registering the second tool rather than to the cue width.
    """
    from palintel.routing import StubRouter
    utterance = "Hey Pal, where can I find Suzaku?"
    r = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, pal_spawns=False)
    call = r.route(utterance, kb.lexicon.rank(utterance))
    assert isinstance(call, Decline)
    assert "Suzaku" in call.reason


def test_wake_word_never_matches_an_entity(kb: KnowledgeBase):
    """"pal" scores 0.57 against "coal" and appears in every single utterance."""
    ranked = kb.lexicon.rank("hey pal", limit=10)
    assert not any(c.canonical == "coal" for c in ranked)


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


def test_crude_oil_is_located_and_says_it_is_not_mined(kb: KnowledgeBase):
    """The card asserted "there are no map locations to give you" until 2026-08-12. There
    are 185 oil fields, and the game's own item text says so: "Obtained by installing a
    Crude Oil Extractor in an oil field."

    The second half is the part that keeps it honest. These coordinates are places to
    BUILD on, and a list of them under "Crude Oil locations" reads as an instruction to go
    and swing a pickaxe unless the card says otherwise.
    """
    r = find_resource_nodes(kb, "crude_oil", limit=3)
    assert r.nodes and r.provided
    card = resource_card(r)
    text = card.to_text().lower()
    assert "oil field" in text and "extractor" in text
    # The unit on each coordinate row, which is where "deposit" would be a claim rather
    # than part of the sentence explaining that these are not deposits.
    rows = [ln for ln in card.lines if ln.startswith("**")]
    assert rows and all("field" in ln and "deposit" not in ln for ln in rows)


def test_a_mined_resource_carries_no_extractor_note(kb: KnowledgeBase):
    """The note is per-resource and derived, not printed on everything."""
    r = find_resource_nodes(kb, "coal", limit=3)
    assert r.nodes and not r.provided
    assert "extractor" not in resource_card(r).to_text().lower()


def test_decline_card_names_unrecognised_token_when_known():
    card = decline_card(Decline(reason="test", unrecognized="adamantium"))
    assert "adamantium" in card.to_text()


def test_player_state_is_injected_not_parsed(pipe: Pipeline):
    """'nearest' must resolve against live state, not text."""
    far = pipe.handle("where's the nearest coal", PlayerState(player_coords=(800.0, 400.0)))
    near = pipe.handle("where's the nearest coal", PlayerState(player_coords=(20.0, -153.0)))
    assert far.card.lines[0] != near.card.lines[0]


# ------------------------------------------------------------- transport fallback

class _Fixed:
    """A router that always returns the same thing. Stands in for a hosted backend."""

    def __init__(self, result, usage=None):
        self.name = "fixed"
        self._result = result
        self.calls = 0
        # A hosted backend sets this on a call and leaves it set. Modelled here because
        # that stickiness is exactly what `FastPathRouter.last_usage` has to defend
        # against - see the test below.
        self._usage = usage
        self.last_usage = None

    def route(self, utterance, candidates, context=None):
        self.calls += 1
        self.last_usage = self._usage
        # A real backend stamps the usage onto what it returns as well as onto itself.
        # Modelled here because that is what makes the cost survive a concurrent caller.
        return replace(self._result, usage=self._usage)


def test_timeout_falls_through_to_the_stub(kb: KnowledgeBase):
    """A router that never answered must still produce a card it can stand behind."""
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    r = FallbackRouter(_Fixed(Decline(reason="gemini unreachable", transient=True)), stub)
    out = Pipeline(kb, r).handle("where's the nearest coal")
    assert isinstance(out.call, ToolCall)
    assert out.call.args["resource"] == "coal"
    # The reason the primary failed has to survive into the rationale, or a run of
    # timeouts looks like the stub simply being the configured router.
    assert "unreachable" in out.call.rationale


def test_considered_decline_is_never_second_guessed(kb: KnowledgeBase):
    """The stub knows strictly less than the model. Re-deciding on less is how a
    considered 'no' turns into a confidently wrong 'yes'."""
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    primary = _Fixed(Decline(reason="two candidates equally plausible"))
    r = FallbackRouter(primary, stub)
    out = Pipeline(kb, r).handle("where's the nearest coal")
    assert isinstance(out.call, Decline)
    assert out.call.reason == "two candidates equally plausible"


def test_fast_path_answers_without_calling_the_model(kb: KnowledgeBase):
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    model = _Fixed(Decline(reason="should never be reached"))
    out = Pipeline(kb, FastPathRouter(stub, model)).handle("where's the nearest coal")
    assert out.call.args["resource"] == "coal"
    assert model.calls == 0


def test_a_fast_path_answer_reports_no_usage_even_after_a_model_call(kb: KnowledgeBase):
    """The bug that made the 2026-08-12 session read $0.3344 when it cost $0.0880.

    A hosted backend sets `last_usage` on a call and never clears it, and `FastPathRouter`
    forwarded the attribute straight through. So every fast-path answer AFTER the first
    model call was charged the previous call's cost and counted as having reached the
    model - 55 of 56 queries, when the true figure was 16. Both numbers the spend ledger
    exists to produce, wrong in the direction that drains a prepaid balance early.
    """
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    model = _Fixed(Decline(reason="considered"), usage=object())
    router = FastPathRouter(stub, model)

    # Something only the model can take, so `model.last_usage` is left set.
    router.route("how do I breed a Vanwyrm", kb.lexicon.rank("how do I breed a Vanwyrm"))
    assert model.calls == 1 and model.last_usage is not None
    assert router.last_usage is not None, "a real model call must still be billable"

    # Now one the stub answers. No call was made, so there is nothing to bill.
    router.route("where's the nearest coal", kb.lexicon.rank("where's the nearest coal"))
    assert model.calls == 1
    assert router.last_usage is None


def test_a_stub_restatement_decline_is_not_billed(kb: KnowledgeBase):
    """`needs_restatement` never reaches the model by design, so it costs nothing."""
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    model = _Fixed(Decline(reason="considered"), usage=object())
    router = FastPathRouter(stub, model)
    router.route("what about the alpha?", [])
    assert model.calls == 0 and router.last_usage is None

def test_usage_travels_on_the_answer_not_on_the_router(kb: KnowledgeBase):
    """The charge must be readable from the Outcome alone.

    `bot._answer` used to read `pipe.router.last_usage` AFTER `run_in_executor` returned.
    That is a shared slot on an object every caller uses, so the value it holds by then
    belongs to whichever query most recently entered `route` - not to this one.
    """
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    spent = object()
    router = FastPathRouter(stub, _Fixed(Decline(reason="considered"), usage=spent))
    pipe = Pipeline(kb, router)

    assert pipe.handle("how do I breed a Vanwyrm").usage is spent
    # The stub answers this one outright, so no model call and nothing to bill.
    assert pipe.handle("where's the nearest coal").usage is None


def test_concurrent_queries_are_not_billed_each_others_tokens(kb: KnowledgeBase):
    """The 2026-08-13 defect: the 2026-08-12 ledger bug, arriving again by another route.

    A fast-path query and a model query overlap on one shared router. Reading the cost off
    the router after the fact gives whichever value the OTHER thread left there, so a real
    model call gets logged as a $0 fast-path row - or a free answer gets charged. Both
    figures the ledger exists to produce, wrong in the direction that empties a prepaid
    balance early, which is exactly what happened the first time.

    The barrier forces the interleaving that makes this deterministic rather than hoping
    the scheduler produces it.
    """
    spent = object()
    entered = threading.Event()
    proceed = threading.Event()

    class _Slow:
        """A model backend that is inside `route` while the other thread runs."""
        name = "slow"

        def __init__(self):
            self.last_usage = None

        def route(self, utterance, candidates, context=None):
            entered.set()
            # Hold the model call open until the fast-path query has been and gone.
            proceed.wait(timeout=5)
            self.last_usage = spent
            return Decline(reason="considered", usage=spent)

    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    router = FastPathRouter(stub, _Slow())
    pipe = Pipeline(kb, router)
    out: dict[str, object] = {}

    def model_query():
        out["model"] = pipe.handle("how do I breed a Vanwyrm")

    t = threading.Thread(target=model_query)
    t.start()
    assert entered.wait(timeout=5), "the model backend never started"

    # Runs entirely inside the other query's model call, and must not be billed for it.
    fast = pipe.handle("where's the nearest coal")
    proceed.set()
    t.join(timeout=5)

    assert fast.usage is None, "a fast-path answer was billed a concurrent model call"
    assert out["model"].usage is spent, "a model call lost its charge to a concurrent query"

    # And the point of the whole change: the OLD read is demonstrably wrong here, so this
    # test bites rather than merely agreeing with the new code. The fast-path query reset
    # `_went_to_model` to False while the model query was still inside `route`, so the
    # model call that genuinely happened reports None and would be logged as a $0
    # fast-path row - under-reporting spend and over-reporting how much of play never
    # reaches the model. Both are the 2026-08-12 failure, exactly.
    assert router.last_usage is None
    assert out["model"].usage is not router.last_usage


def test_fast_path_defers_anything_it_is_not_sure_of(kb: KnowledgeBase):
    """The stub claiming a query it cannot answer is the whole risk of the fast path."""
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    model = _Fixed(ToolCall(name="find_resource_nodes", args={"resource": "ore"}))
    pipe = Pipeline(kb, FastPathRouter(stub, model))
    # Names a resource, but asks about inventory rather than location - the model
    # declined this one in the A5 run, and the stub must not answer it either.
    pipe.handle("do I have enough sulfur for this")
    pipe.handle("how do I breed a Vanwyrm")
    assert model.calls == 2


@pytest.mark.parametrize("cues,claims", [("standard", False), ("wide", True)])
def test_cue_width_is_configurable(kb: KnowledgeBase, cues: str, claims: bool):
    """'any sulfur around here' is the phrasing 'wide' exists to catch."""
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, cues=cues)
    call = stub.route("hey pal, is there any sulfur around here",
                      kb.lexicon.rank("hey pal, is there any sulfur around here"))
    assert isinstance(call, ToolCall) == claims


def test_unknown_cue_set_fails_loudly(kb: KnowledgeBase):
    with pytest.raises(ValueError, match="unknown cue set"):
        StubRouter(kb.lexicon, cues="agressive")


def test_both_failing_reports_the_transport_not_the_vocabulary(kb: KnowledgeBase):
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    r = FallbackRouter(_Fixed(Decline(reason="gemini unreachable", transient=True)), stub)
    out = Pipeline(kb, r).handle("how do I breed a Vanwyrm")
    assert isinstance(out.call, Decline)
    assert out.call.reason == "gemini unreachable"
    assert out.call.known_options  # still names what it can answer


# ------------------------------------------------------------------ stt hotwords

def test_hotwords_hoist_the_spoken_resources_only(kb: KnowledgeBase):
    """Bias decays along the list, and `sorted()` buried the resources at the bottom.

    They are the only lowercase entries, so ASCII put all 313 capitalised Pal names ahead
    of the nouns Q1 answers about. Phase 1 hoisted every resource; Phase 2 re-measured
    that over 185 clips with a Pal tool registered and found hoisting is a BUDGET - the
    set had grown from 5 to 19, and the extra fourteen cost 8 Pal clips. Hoisting only
    the five the eval set exercises scores 19/19 and 101/166, better than every other
    ordering on both classes at once.
    """
    from palintel.stt import VOICE_RESOURCES, hotword_order

    order = hotword_order(kb.lexicon)
    assert order[:len(VOICE_RESOURCES)] == list(VOICE_RESOURCES)
    # The rest of the resources are still present, behind the Pals rather than ahead.
    assert set(order[len(VOICE_RESOURCES):]) & set(kb.lexicon.resources())
    # Reordered, not filtered: Phase 2 needs every Pal name still in the list.
    assert set(order) == set(kb.lexicon.canonical_names)
    assert len(order) == len(set(order))


@pytest.mark.parametrize("utterance, resource", [
    # Both were the slowest ANSWERED queries of a real session: clean entities that
    # paid a full model round trip because the cue list had never seen the phrasing.
    ("hey pal, can I get coal at this level?", "coal"),
    ("hey pal, what's the best place to farm quartz?", "quartz"),
])
def test_cues_cover_the_phrasings_a_session_actually_used(kb: KnowledgeBase,
                                                          utterance, resource):
    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    call = stub.route(utterance, kb.lexicon.rank(utterance))
    assert isinstance(call, ToolCall)
    assert call.args["resource"] == resource


def test_widening_never_claims_the_inventory_question(kb: KnowledgeBase):
    """Names a resource, is not a location question, and the model declined it too.

    It has stayed deferred through every widening, which is the evidence that the cue
    gate discriminates rather than just matching resource nouns.
    """
    for cues in ("standard", "proximity", "wide"):
        stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, cues=cues)
        utterance = "hey pal, do I have enough sulfur for this?"
        assert isinstance(stub.route(utterance, kb.lexicon.rank(utterance)), Decline)


# --------------------------------------------------------- permissive backstop

def test_backstop_rescues_what_the_fast_path_would_not(kb: KnowledgeBase):
    """The backstop must be MORE permissive than the fast path, or it is dead code.

    An identical stub cannot rescue anything: the fast path asked it first, so whatever
    reaches the model is by definition something it already declined. The first version
    of this wiring shared one instance and could not rescue a single query while its
    docstring claimed it answered clear resource queries outright.

    "where's the nearest goal?" is verbatim from a session - coal heard as "goal",
    ranking 0.75, under the fast path's 0.78 and over the backstop's 0.68.
    """
    from palintel.pipeline import build_router
    from palintel.routing import BACKSTOP_CONFIDENT, MIN_CONFIDENT

    assert BACKSTOP_CONFIDENT < MIN_CONFIDENT

    locatable = {n.resource for n in kb.nodes}
    fast = StubRouter(kb.lexicon, locatable)
    backstop = StubRouter(kb.lexicon, locatable, cues="wide",
                          resource_floor=BACKSTOP_CONFIDENT)
    utterance = "hey pal, where's the nearest goal?"
    cands = kb.lexicon.rank(utterance)

    assert isinstance(fast.route(utterance, cands), Decline)      # too weak to preempt
    rescued = backstop.route(utterance, cands)
    assert isinstance(rescued, ToolCall)                          # good enough to salvage
    assert rescued.args["resource"] == "coal"

    router = FastPathRouter(fast, FallbackRouter(
        _Fixed(Decline(reason="gemini unreachable", transient=True)), backstop))
    out = Pipeline(kb, router).handle(utterance)
    assert isinstance(out.call, ToolCall)
    assert out.call.args["resource"] == "coal"


def test_backstop_does_not_loosen_the_pal_guard(kb: KnowledgeBase):
    """A permissive backstop answers weaker RESOURCE matches. It must not become quicker
    to decide an utterance names a Pal, which is what happened when one constant gated
    both - and which now steals the query from the resource branch rather than merely
    giving up on it."""
    locatable = {n.resource for n in kb.nodes}
    backstop = StubRouter(kb.lexicon, locatable, cues="wide",
                          resource_floor=BACKSTOP_CONFIDENT)
    utterance = "hey pal, where can I find Suzaku?"
    call = backstop.route(utterance, kb.lexicon.rank(utterance))
    assert isinstance(call, ToolCall) and call.name == "find_pal_spawns"

    # The guard's own bar is unmoved by the lower resource floor: "near a store" ranks
    # ore at 0.75 against a Pal at 0.75, and must still reach the resource branch.
    heard = "we're sitting near a store"
    out = backstop.route(heard, kb.lexicon.rank(heard))
    assert isinstance(out, ToolCall) and out.name == "find_resource_nodes"


def test_backstop_floor_stays_above_where_wrong_answers_start(kb: KnowledgeBase):
    """0.64 put a resource card on "can I get Zendelord before the first tower"."""
    locatable = {n.resource for n in kb.nodes}
    utterance = "Hey pal, can I get Zendelord before the first tower?"
    cands = kb.lexicon.rank(utterance)
    safe = StubRouter(kb.lexicon, locatable, cues="wide",
                      resource_floor=BACKSTOP_CONFIDENT)
    assert isinstance(safe.route(utterance, cands), Decline)
