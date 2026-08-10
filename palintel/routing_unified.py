"""One tool for every query class, instead of one tool each.

[01-architecture.md](../Docs/01-architecture.md) §7 note 4 measured the cost of the
per-class registry and named this as the lever: **tool schemas, not tokens of thought,
are what a query costs.** The Pal enum is ~2,630 tokens and every tool naming a Pal
carries its own copy, so completing the seven query classes makes each request 25x more
expensive before a single token is generated. A single `answer_query` carries one copy of
each enum.

The doc is equally clear that the trade is untested: it costs "the per-tool descriptions
that currently help the router choose", and A5 must be re-measured across the change
rather than assumed neutral. This module exists to make that measurement possible with
both registries live at once, so the comparison is same-day and same-config rather than
against a number in a document.

**A boss is a free string, not a Pal.** The first attempt folded every entity into the
Pal array, and the run that followed was uninterpretable because of it: "should I use
Cremis against the first tower" came back as `[Cremis, Zoe & Grizzbolt]`, scoring the
tower as an invented entity. The per-class `evaluate_counter` takes `target` as verbatim
text precisely so the router does not have to resolve a tower to a species, and dropping
that distinction measured the schema rather than the router. It is kept here.

**Slots are 0..n arrays, not nullable enums.** `pal_spawn_schema`'s docstring already
found the landmine: strict tool use expresses an optional parameter as a nullable type
that is still `required`, and that has no clean form for an enum. An array with zero
items says "not applicable" without inventing one, and it lets `check_breeding_pair` name
two Pals against a single copy of the enum - which is the whole point of consolidating.
"""
from __future__ import annotations

from typing import Any

# query_class -> (the tool this used to be, how its arguments map back).
#
# Translation matters more than it looks. Everything downstream - the dispatcher, the
# scorer, conversation memory - keys on the old names, and rewriting all of them to
# measure a routing change would confound the measurement with a refactor. One tool on
# the wire, the existing vocabulary behind it.
CLASS_TO_TOOL: dict[str, str] = {
    "resource_location": "find_resource_nodes",
    "pal_location": "find_pal_spawns",
    "pal_drops": "find_pal_drops",
    "item_source": "find_item_source",
    "breeding_combo": "get_breeding_combo",
    "breeding_pair": "check_breeding_pair",
    "pal_info": "get_pal_info",
    "compare_pals": "compare_pals",
    "boss_counter": "evaluate_counter",
}

# What the per-tool descriptions used to say, compressed into one line each. This is the
# part of the trade the measurement is about: with seven tools the router picked between
# seven descriptions, and here it picks between seven enum values.
CLASS_HELP: dict[str, str] = {
    "resource_location": "where to find, mine or farm a resource (coal, ore, quartz)",
    "pal_location": "where to find, catch or encounter a Pal species",
    "pal_drops": "what items a Pal yields when defeated or captured",
    "item_source": "which Pals drop a named item (Flame Organ, Leather, Bone)",
    "breeding_combo": "which parent Pals breed to produce a target Pal",
    "breeding_pair": "what two named Pals produce when bred together",
    "pal_info": "a Pal's element, stats or work suitability",
    "compare_pals": "which of two named Pals is better at something",
    "boss_counter": "which Pal to use against a boss or tower",
}

PRODUCTION_CLASSES = ("resource_location", "pal_location", "pal_drops",
                      "item_source")


def unified_schema(resources: list[str], pals: list[str],
                   items: list[str] | None = None,
                   classes: tuple[str, ...] = PRODUCTION_CLASSES) -> dict[str, Any]:
    """One tool covering `classes`, with one copy of each enum.

    `classes` is a parameter rather than a constant because production dispatches two of
    these and the A5 harness registers all seven. Offering a class the caller cannot
    dispatch would measure the registry rather than the router - the same mistake the
    harness's own docstring records.
    """
    lines = "\n".join(f"- {c}: {CLASS_HELP[c]}" for c in classes)
    return {
        "name": "answer_query",
        "description": (
            "Answer a Palworld question. Choose the query_class that matches what the "
            "player asked, and fill only the slots that class needs:\n" + lines
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query_class": {
                    "type": "string",
                    "enum": list(classes),
                    "description": "Which kind of question this is.",
                },
                "pals": {
                    "type": "array",
                    "items": {"type": "string", "enum": pals},
                    "description": (
                        "The Pal or Pals the question names, in the order spoken. "
                        "Empty when the question does not name one."
                    ),
                },
                "resources": {
                    "type": "array",
                    "items": {"type": "string", "enum": resources},
                    "description": (
                        "The resource the question names. Empty when it names none."
                    ),
                },
                "items_named": {
                    "type": "array",
                    "items": {"type": "string", "enum": items or []},
                    "description": (
                        "The item the question asks the SOURCE of - \"who drops Flame "
                        "Organ\". Empty otherwise. Many of these are ordinary English "
                        "words (Arrow, Bone, Leather); only fill this when the player is "
                        "asking where an item comes from."
                    ),
                },
                "target": {
                    "type": ["string", "null"],
                    "description": (
                        "The boss or tower the question names, VERBATIM as spoken - "
                        "not resolved to a Pal name. Null when none is named."
                    ),
                },
                "max_player_level": {
                    "type": ["integer", "null"],
                    "description": (
                        "Only return results safe at or below this player level. Set it "
                        "only when the player states a level; otherwise null."
                    ),
                },
            },
            "required": ["query_class", "pals", "resources", "items_named", "target",
                         "max_player_level"],
            "additionalProperties": False,
        },
    }


# Which slot each old tool's arguments came from, in order.
_ARGS: dict[str, tuple[str, ...]] = {
    "find_resource_nodes": ("resource",),
    "find_pal_spawns": ("pal",),
    "find_pal_drops": ("pal",),
    "find_item_source": ("item",),
    "get_breeding_combo": ("target",),
    "check_breeding_pair": ("parent_a", "parent_b"),
    "get_pal_info": ("pal",),
    "compare_pals": ("pal_a", "pal_b"),
    "evaluate_counter": ("pal",),
}


def unpack(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """`answer_query(...)` back into the tool name and arguments the rest of the code uses.

    A pass-through for any other tool, so a backend can apply it unconditionally and the
    per-class registry keeps behaving exactly as it did.
    """
    if name != "answer_query":
        return name, args

    tool = CLASS_TO_TOOL.get(args.get("query_class") or "")
    if tool is None:
        # A class the model invented, or one this registry did not offer. Returned
        # unchanged so the caller's existing "unregistered tool" path handles it rather
        # than this function guessing.
        return name, args

    named_pals = list(args.get("pals") or [])
    values = (list(args.get("resources") or []) + list(args.get("items_named") or [])
              + named_pals)
    out: dict[str, Any] = dict(zip(_ARGS[tool], values))

    # A question may name more Pals than the old single-slot tools could hold - "what do
    # I get from Astralym and Mycora" is two. The consolidated schema can express that
    # and the per-class one never could, so the extras are carried rather than dropped;
    # a dispatcher that only understands `pal` still finds it in the first slot.
    if len(named_pals) > 1 and _ARGS[tool] == ("pal",):
        out["pals"] = named_pals
    if tool == "find_resource_nodes" and args.get("max_player_level") is not None:
        out["max_player_level"] = args["max_player_level"]
    if tool == "evaluate_counter" and args.get("target") is not None:
        out["target"] = args["target"]
    return tool, out
