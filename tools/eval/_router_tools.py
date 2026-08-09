"""Tool schemas registered only by the A5 harness.

None of these are dispatched in Phase 1. They exist because the A5 measurement is of
*entity resolution*, and a router cannot resolve an entity it has nowhere to put. With
only find_resource_nodes and find_pal_spawns registered, 24 of the 36 utterance prompts
have no matching tool and can do nothing but decline - which scores the tool registry
and reports it as a router failure.

The prompt set is six templates rotated across Pals, so the registry has to cover them:

    "how do I breed X" / "breeding combo for X"   -> get_breeding_combo
    "can I breed X with Y"                        -> check_breeding_pair
    "what element is X" / "is X good for mining"  -> get_pal_info
    "is X better than Y for handiwork"            -> compare_pals
    "should I use X against the first tower"      -> evaluate_counter

Descriptions are written the way the real tools' would be, because the router selects on
them. Schemas are `strict: true` against the lexicon enum for the same reason the
production tool is: an entity outside the lexicon cannot come back at all.
"""
from __future__ import annotations

from typing import Any


def _pal(pals: list[str], desc: str) -> dict[str, Any]:
    return {"type": "string", "enum": pals, "description": desc}


def eval_tool_schemas(pals: list[str]) -> list[dict[str, Any]]:
    """Every non-Q1 tool the prompt set exercises."""
    return [
        {
            "name": "get_breeding_combo",
            "description": (
                "Find which parent Pals breed to produce a given Pal. Call this when the "
                "player asks how to breed, hatch, or obtain a specific Pal from an egg."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"pal": _pal(pals, "The Pal the player wants to produce.")},
                "required": ["pal"],
                "additionalProperties": False,
            },
        },
        {
            "name": "check_breeding_pair",
            "description": (
                "Determine what two specific Pals produce when bred together. Call this "
                "when the player names both parents."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "parent_a": _pal(pals, "First parent."),
                    "parent_b": _pal(pals, "Second parent."),
                },
                "required": ["parent_a", "parent_b"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_pal_info",
            "description": (
                "Look up a Pal's elements, base stats, and work suitability. Call this "
                "for questions like \"what element is X\", \"is X good for mining\", or "
                "\"what can X do at my base\"."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {"pal": _pal(pals, "Which Pal to look up.")},
                "required": ["pal"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compare_pals",
            "description": (
                "Compare two Pals for a particular job or role - for example \"is X "
                "better than Y for handiwork\"."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "pal_a": _pal(pals, "First Pal."),
                    "pal_b": _pal(pals, "Second Pal."),
                },
                "required": ["pal_a", "pal_b"],
                "additionalProperties": False,
            },
        },
        {
            "name": "evaluate_counter",
            "description": (
                "Judge whether a Pal is a good choice against a specific boss or tower "
                "boss, using element matchups. Call this for \"should I use X against "
                "the first tower\" or \"what beats <boss>\"."
            ),
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "pal": _pal(pals, "The Pal the player is considering."),
                    "target": {
                        "type": ["string", "null"],
                        "description": (
                            "The boss or tower named, verbatim, or null if unstated."
                        ),
                    },
                },
                "required": ["pal", "target"],
                "additionalProperties": False,
            },
        },
    ]
