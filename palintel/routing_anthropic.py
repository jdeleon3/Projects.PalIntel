"""Claude-backed intent router.

Implements the RouterBackend protocol from routing.py. Kept in its own module so the
stub path never imports the Anthropic SDK.

The router's only job is turning an utterance into a typed tool call. It never sees a
coordinate, a stat, or a breeding pair, and nothing it returns reaches a card unmediated
(Docs/adr/0002-llm-as-router.md).

Two model-specific choices are load-bearing rather than incidental:

  * Thinking stays ON. With `thinking: {"type": "disabled"}` Claude Opus 5 occasionally
    writes a tool call into its visible text instead of emitting a tool_use block: the
    turn succeeds, the call never runs, and nothing raises. For a router that is a
    silent no-op, so latency is controlled with `effort` instead of by disabling
    thinking.
  * `strict: true` on the tool schema. This makes the API enforce the lexicon-generated
    enum, so an entity outside the lexicon cannot come back at all - ADR-0016's
    "constrained to a lexicon enum" as a guarantee rather than an instruction.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from .knowledge import Candidate, Lexicon
from .routing import ROUTING_POLICY
from .tools import Decline, ToolCall

log = logging.getLogger("palintel.routing.claude")

MODEL = "claude-opus-5"
# Routing is a small classification task on a 2.5s end-to-end budget. Low effort keeps
# latency down while leaving thinking enabled.
EFFORT = "low"
# Small ceiling: the output is one tool call. Headroom is for thinking, not prose.
MAX_TOKENS = 4096
# Pre-4.6 models reject both `adaptive` thinking and `effort` with a 400, and take a fixed
# token budget instead. Kept as a table rather than a version check so an unknown model
# fails loudly on the API rather than silently routing without thinking - the comparison
# is only fair if both models are allowed to think.
LEGACY_THINKING = {"claude-haiku-4-5": 2048}


def _reasoning_params(model: str) -> dict:
    """Thinking configuration for `model`, in that model's own dialect."""
    budget = LEGACY_THINKING.get(model)
    if budget is None:
        return {"thinking": {"type": "adaptive"},
                "output_config": {"effort": EFFORT}}
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}

SYSTEM = f"""\
You route Palworld voice and text queries to typed tools. You are one stage in a \
pipeline: your only output is a tool call, or nothing. Call a tool when you can \
identify both the intent and its parameters; otherwise return no tool call and say \
briefly what was unclear.

{ROUTING_POLICY}\
"""


# USD per million tokens: (input, output). Cache writes bill at 1.25x input, reads at
# 0.1x. Kept here so a run can price itself rather than being estimated after the fact.
PRICES = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass(frozen=True)
class Usage:
    """Token counts and cost for one routing call.

    Recorded on every response, including declines. Declines were previously the only
    path that logged no usage, and they are the *expensive* ones: a decline is what the
    model produces after thinking hardest about an ambiguous entity. Estimating run cost
    from the routing calls alone understates it several-fold.
    """
    input: int
    output: int
    cache_read: int
    cache_write: int
    model: str

    @property
    def usd(self) -> float:
        inp, out = PRICES.get(self.model, PRICES["claude-opus-5"])
        return ((self.input * inp + self.cache_write * inp * 1.25
                 + self.cache_read * inp * 0.1 + self.output * out) / 1e6)

    def __str__(self) -> str:
        return (f"[in {self.input} +{self.cache_read} cached, out {self.output}, "
                f"${self.usd:.4f}]")

    @classmethod
    def of(cls, response: Any, model: str) -> "Usage":
        u = response.usage
        return cls(input=u.input_tokens, output=u.output_tokens,
                   cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                   cache_write=getattr(u, "cache_creation_input_tokens", 0) or 0,
                   model=model)


def pal_spawn_schema(pals: list[str]) -> dict[str, Any]:
    """Q2's tool. Not dispatched in Phase 1 - registered only by the A5 harness.

    Without it, every Pal question has nowhere to put its entity and can only be scored
    "declined", which measures the tool registry rather than entity resolution.
    """
    return {
        "name": "find_pal_spawns",
        "description": (
            "Locate where a Pal spawns in the wild. Call this when the player asks "
            "where to find, catch, or encounter a specific Pal."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "pal": {"type": "string", "enum": pals,
                        "description": "Which Pal to locate."},
            },
            "required": ["pal"],
            "additionalProperties": False,
        },
    }


def _tool_schema(resources: list[str]) -> dict[str, Any]:
    """Tool definition with the resource enum generated from the live lexicon.

    Generating the enum rather than hardcoding it means the router's vocabulary and the
    corrector's vocabulary cannot drift apart.
    """
    return {
        "name": "find_resource_nodes",
        "description": (
            "Locate resource node clusters on the Palworld map. Call this when the "
            "player asks where to find, mine, or farm a resource - for example "
            "\"where's the nearest coal\", \"find me an ore spot\", or \"show me "
            "quartz near my base\"."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "resource": {
                    "type": "string",
                    "enum": resources,
                    "description": "Which resource to locate.",
                },
                "max_player_level": {
                    "type": ["integer", "null"],
                    "description": (
                        "Only return nodes safe at or below this player level. Set it "
                        "only when the player states a level; otherwise null."
                    ),
                },
            },
            "required": ["resource", "max_player_level"],
            "additionalProperties": False,
        },
    }


class ClaudeRouter:
    """RouterBackend backed by the Anthropic Messages API."""

    name = f"claude:{MODEL}"

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None,
                 api_key: str | None = None, extra_tools: list[dict] | None = None,
                 model: str = MODEL):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "anthropic SDK not installed:  pip install -r requirements.txt") from e

        self._anthropic = anthropic
        # A bare client also resolves an `ant auth login` profile, so do not require the
        # env var to be set - only pass a key when one was supplied explicitly.
        self._client = (anthropic.Anthropic(api_key=api_key) if api_key
                        else anthropic.Anthropic())
        self._resources = sorted(locatable if locatable is not None
                                 else set(lexicon.resources()))
        self._model = model
        self.name = f"claude:{model}"
        self._tools = [_tool_schema(self._resources), *(extra_tools or [])]
        self.last_usage: Usage | None = None

    def _user_content(self, utterance: str, candidates: list[Candidate]) -> str:
        if candidates:
            hints = "\n".join(
                f"  {c.score:.2f}  {c.canonical}  ({c.kind}, matched {c.matched_text!r})"
                for c in candidates)
        else:
            hints = "  (none)"
        return (f"Utterance:\n  {utterance}\n\n"
                f"Candidate entities, best first:\n{hints}")

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=MAX_TOKENS,
                # Cache the stable prefix. Render order is tools -> system -> messages,
                # so a breakpoint on the system block covers the tool schemas too - and
                # those dominate: the Pal enum alone is 313 names shipped every request.
                # The breakpoint must NOT go on the user turn, which differs every time.
                system=[{"type": "text", "text": SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                **_reasoning_params(self._model),
                tools=self._tools,
                messages=[{"role": "user",
                           "content": self._user_content(utterance, candidates)}],
            )
        except self._anthropic.RateLimitError:
            return Decline(reason="router rate limited - try again shortly")
        except self._anthropic.APIStatusError as e:
            log.error("router API error %s: %s", e.status_code, e.message)
            return Decline(reason="router unavailable")
        except self._anthropic.APIConnectionError:
            return Decline(reason="router unreachable - check your connection")

        self.last_usage = Usage.of(response, self._model)

        # A refusal carries no usable content; check before reading blocks.
        if response.stop_reason == "refusal":
            log.warning("router refused: %s", response.stop_details)
            return Decline(reason="router declined to answer that")

        tool_use = next((b for b in response.content if b.type == "tool_use"), None)
        if tool_use is None:
            said = " ".join(b.text for b in response.content if b.type == "text").strip()
            log.info("router declined %s: %s", self.last_usage,
                     said or "(no reason given)")
            return Decline(reason=said or "no matching query type",
                           known_options=self._resources)

        # strict:true guarantees the schema, but null-valued optionals still arrive as
        # keys - drop them so the dispatcher's defaults apply.
        args = {k: v for k, v in dict(tool_use.input).items() if v is not None}
        log.info("router -> %s(%s) %s", tool_use.name, args, self.last_usage)
        return ToolCall(name=tool_use.name, args=args,
                        rationale=f"{self.name} chose {tool_use.name}")


def available(api_key: str | None = None) -> bool:
    """True when the SDK is importable and some credential is resolvable."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    if api_key or os.environ.get("ANTHROPIC_API_KEY"):
        return True
    # An `ant auth login` profile also authenticates a bare client.
    from pathlib import Path
    return (Path.home() / ".config" / "anthropic" / "credentials").exists()
