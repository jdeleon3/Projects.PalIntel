"""Tool contract — the typed boundary between understanding and answering.

The router's ONLY job is producing one of these calls. Everything past this point is
deterministic: no model touches a coordinate, a stat, or a breeding pair.
See Docs/01-architecture.md section 4 and Docs/adr/0002-llm-as-router.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    # Why the router chose this. Surfaced in logs and `/palintel status`, never on cards.
    rationale: str = ""


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
def schemas(resources: list[str]) -> list[dict[str, Any]]:
    return [find_resource_nodes_schema(resources)]
