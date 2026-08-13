"""Tool contract — the typed boundary between understanding and answering.

The router's ONLY job is producing one of these calls. Everything past this point is
deterministic: no model touches a coordinate, a stat, or a breeding pair.
See Docs/01-architecture.md section 4 and Docs/adr/0002-llm-as-router.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# What a model call cost, carried BY THE ANSWER rather than left on the router.
#
# `last_usage` was instance state on a shared router, read by the caller *after*
# `run_in_executor` returned - so two overlapping queries interleaved and one was billed
# the other's tokens, or None. The same defect twice: the 2026-08-12 session found
# `FastPathRouter` forwarding a stale `last_usage` and reported $0.3344 over 56 queries
# when it was $0.0880 over 16.
#
# A frozen field on the returned call cannot go stale and cannot be read by the wrong
# thread, because there is no shared slot to read. `None` stays meaningful and is not a
# gap: it means *no model was called*, which is what `charge_from(None, ...)` logs as a
# $0 fast-path row, and what answers "how much of play never reaches the model at all".
Usage = Any


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    # Why the router chose this. Surfaced in logs and `/palintel status`, never on cards.
    rationale: str = ""
    # A second call to answer alongside this one, for questions with two right answers
    # in two different tools. "Where can I find something to beat Anubis" is a location
    # question and a counter question at once, and picking one is a coin flip on which
    # TIER the player gets - so both are answered instead.
    #
    # Optional and defaulted, so every router that cannot produce one is unaffected:
    # only the deterministic fast path sets it, because only it can see that two cue
    # families fired. A model that wants two answers should be asked twice.
    then: "ToolCall | None" = None
    # What the model call that produced this cost, or None when none was made. See the
    # note above `Usage`: this travels with the answer precisely so nothing has to read
    # it off a router that another thread is already using.
    usage: Usage = None


@dataclass(frozen=True)
class Decline:
    """No confident interpretation. Rendered as an honest card, never as a guess.

    `unrecognized` must be a token the user actually said that we could not place - not
    the best fuzzy match, which is a different thing and actively misleading. Asking for
    "adamantium" once reported `nearest` as the unmatched token, because that was where
    the top-scoring candidate happened to match.
    """
    reason: str
    unrecognized: str | None = None
    # What the system *can* answer, when naming that is more useful than an apology.
    known_options: list[str] = field(default_factory=list)
    # The router never reached a judgement - it timed out, was rate limited, or could not
    # be reached. Distinct from a considered decline, because a backstop router can
    # usefully retry this one and cannot usefully second-guess the other.
    transient: bool = False
    # The utterance referred back to something that is no longer remembered. A third kind
    # of decline, because it asks for something specific and achievable - say the name
    # again - rather than reporting a failure. ADR-0013 requires expired context to be
    # named rather than silently ignored: answering "what about the alpha" against no
    # referent is how a confident card about the wrong Pal gets made.
    needs_restatement: bool = False
    # A decline can cost money too - the model was asked and said no - so it carries the
    # same field a ToolCall does, for the same reason. Omitting it here would under-report
    # spend by exactly the queries the router found hardest.
    usage: Usage = None


def find_resource_nodes_schema(resources: list[str]) -> dict[str, Any]:
    """Tool schema, with the resource enum generated from the live lexicon.

    Generating the enum rather than hardcoding it keeps the router's vocabulary and the
    corrector's vocabulary identical by construction - they cannot drift.
    """
    return {
        "name": "find_resource_nodes",
        "description": (
            "Locate resource node clusters on the Palworld map. Use for questions like "
            "'where is the nearest coal' or 'find me an ore spot'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "resource": {
                    "type": "string",
                    "enum": resources,
                    "description": "Which resource to locate.",
                },
                "max_player_level": {
                    "type": "integer",
                    "description": "Only return nodes safe at or below this level.",
                },
                "limit": {
                    "type": "integer",
                    "description": "How many clusters to return (default 3).",
                },
            },
            "required": ["resource"],
        },
    }


# Registered tools for Phase 1. Exactly one: the vertical slice is deliberately narrow
# so a failure is unambiguously in the pipeline rather than in domain logic
# (Docs/adr/0009-v1-vertical-slice.md).
#
# NOTE: this is not the registry the backends read. They share
# `routing_anthropic._tool_schema` and `pal_spawn_schema` - one definition, rendered into
# Gemini's function declarations and the local grammar from the same Anthropic form.
def schemas(resources: list[str]) -> list[dict[str, Any]]:
    return [find_resource_nodes_schema(resources)]
