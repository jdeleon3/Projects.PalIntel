"""Local router tests — the grammar contract, without a model.

These cover the part of the local backend that is a *claim about architecture* rather
than a claim about model quality: that the 313-name Pal enum leaves the context and
becomes a decoding constraint. That claim is what makes local routing cheap, and none of
it needs a GPU to verify, so it is pinned here rather than left to the eval.

Whether the model picks the *right* entity is a different question and belongs in
tools/eval/score_router.py, which needs a served model and real transcripts.
"""
from __future__ import annotations

import json

import pytest

from palintel.knowledge import Candidate, KnowledgeBase
from palintel.routing_local import LocalRouter, _render_tools
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> LocalRouter:
    return LocalRouter(kb.lexicon, {n.resource for n in kb.nodes}, model="stub")


def _answering(router: LocalRouter, monkeypatch, reply: dict) -> LocalRouter:
    monkeypatch.setattr(router, "_post", lambda path, payload: {
        "message": {"content": json.dumps(reply)},
        "prompt_eval_count": 120, "eval_count": 18,
    })
    return router


def test_grammar_admits_every_entity_and_a_decline(router: LocalRouter,
                                                   kb: KnowledgeBase):
    names = set(router._schema["properties"]["entities"]["items"]["enum"])
    assert set(kb.lexicon.canonical_names) <= names
    assert "decline" in router._schema["properties"]["tool"]["enum"]


def test_enum_stays_out_of_the_prompt(router: LocalRouter, kb: KnowledgeBase):
    """The whole economic argument for routing locally.

    Hosted, the Pal enum is 21,741 tokens of tool schema on every request - ~80% of the
    billed run. If it leaks back into the prompt here, the local path pays that cost in
    context and latency instead of dollars, and the grammar is buying nothing.
    """
    # A mangled utterance, so the canonical spelling can only have come from the
    # candidate list - the case the pipeline actually sees.
    prompt = router._user_content(
        "hey pal, where's the nearest Bellanwar?",
        [Candidate("Bellanoir", "pal", 0.82, "bellanwar")])
    assert prompt.count("Bellanoir") == 1
    # Pals that are neither in the utterance nor the candidate list.
    for absent in ("Grizzbolt", "Omascul", "Kitsun"):
        assert absent not in prompt
    assert len(prompt) < 2000


def test_rendered_tools_carry_intent_but_not_enums():
    block = _render_tools([{
        "name": "get_breeding_combo",
        "description": "Find the parent pair that breeds a given Pal.",
        "input_schema": {"properties": {"pal": {"type": "string",
                                                "enum": ["Kitsun", "Pierdon"]}}},
    }])
    assert "get_breeding_combo(pal)" in block
    assert "breeds a given Pal" in block
    assert "Kitsun" not in block


def test_decline_is_a_decline(router: LocalRouter, monkeypatch):
    r = _answering(router, monkeypatch, {"tool": "decline", "entities": []})
    assert isinstance(r.route("uh", []), Decline)


def test_routed_call_exposes_entities_by_value(router: LocalRouter, monkeypatch):
    """score_router.py collects entities from arg *values*, not slot names."""
    r = _answering(router, monkeypatch,
                   {"tool": "check_breeding_pair", "entities": ["Kitsun", "Pierdon"]})
    call = r.route("can I breed kitsun with pyrdon?", [])
    assert isinstance(call, ToolCall)
    assert set(call.args.values()) == {"Kitsun", "Pierdon"}


def test_entities_outside_the_lexicon_are_dropped(router: LocalRouter, monkeypatch):
    """Defence in depth. The grammar should make this unreachable; if a served model
    ever answers without one, an invented Pal must not reach the scorer as a hit."""
    r = _answering(router, monkeypatch, {"tool": "get_pal_info", "entities": ["Pikachu"]})
    call = r.route("what element is pikachu?", [])
    assert isinstance(call, ToolCall)
    assert call.args == {}


def test_unparseable_output_declines_rather_than_raising(router: LocalRouter,
                                                         monkeypatch):
    monkeypatch.setattr(router, "_post", lambda path, payload: {
        "message": {"content": "not json"}, "prompt_eval_count": 1, "eval_count": 1})
    assert isinstance(router.route("hello", []), Decline)


def test_usage_prices_local_inference_at_zero(router: LocalRouter, monkeypatch):
    r = _answering(router, monkeypatch, {"tool": "decline", "entities": []})
    r.route("uh", [])
    assert r.last_usage.usd == 0.0
    assert r.last_usage.output == 18
