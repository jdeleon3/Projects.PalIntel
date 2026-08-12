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
    "boss_counter": "plan_counters",
    "pal_search": "find_pals_by_attribute",
    "tech_next": "suggest_next_unlock",
    "base_site": "suggest_base_sites",
    "general_knowledge": "lookup_corpus",
    "base_rating": "rate_base_site",
    "base_criteria": "explain_base_criteria",
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
    "pal_info": "a summary of ONE named Pal - \"tell me about X\", \"who is X\", "
                "\"what level is X\". Prefer a narrower class when the question names "
                "one: where it is, what it drops, how to fight it",
    "compare_pals": "which of two named Pals is better at something",
    "boss_counter": "which Pal to use against a boss or tower",
    "pal_search": "which Pals match a DESCRIPTION rather than a name - an element, a "
                  "work job, a level. Use when no specific Pal is named",
    "tech_next": "what TECHNOLOGY to research or unlock next - \"what should I "
                 "research\", \"what can I unlock at level 40\", \"what should I spend "
                 "my technology points on\". Not about Pals at all",
    "base_site": "where to BUILD a base so named resources are inside it - \"where "
                 "should I put a base for coal\". Fill `resources` with what the base is "
                 "for. NOT for \"where's the coal near my base\", which is "
                 "resource_location",
    "general_knowledge": "how a game MECHANIC works - \"how does sanity work\", \"what "
                         "is item rot\", \"explain pal effigies\". Answered by quoting "
                         "the game's own help text, so it needs no slots at all. Choose "
                         "a narrower class whenever the question names a Pal, a resource "
                         "or an item",
    "base_rating": "how good a place ALREADY IS for a base - \"how good is my base "
                   "location\", \"rate this spot\". Set own_base true when they say MY "
                   "base, false when they mean where they are standing. The mirror of "
                   "base_site: that one searches for places, this one judges one. Fill "
                   "`resources` when the question names what the base is FOR - \"is "
                   "this a good spot for a quartz base\"",
    "base_criteria": "what makes a base site good IN GENERAL, naming no place - \"what "
                     "makes a good base\", \"what should I look for\". Needs no slots. "
                     "NOT general_knowledge: the game's help text explains the Palbox "
                     "and says nothing about choosing where to put one",
}

# What the dispatcher can actually answer. `boss_counter` joined on 2026-08-11: the
# pipeline gained a plan_counters branch, so offering it to the model is no longer
# offering a class the caller cannot dispatch - which is the mistake this function's
# docstring warns about. `pal_search` joins the same way and on the same day.
PRODUCTION_CLASSES = ("resource_location", "pal_location", "pal_drops",
                      "item_source", "boss_counter", "pal_search", "pal_info",
                      "tech_next", "base_site", "general_knowledge",
                      "base_rating", "base_criteria")

# The pak's element enum. Nine values, so the cost of carrying it is nothing beside the
# 313-name Pal enum this module exists to stop duplicating. Written out rather than read
# from elements.json because a schema is a contract and should not change shape because
# a data file was regenerated.
ELEMENTS = ("Dark", "Dragon", "Earth", "Electricity", "Fire", "Ice", "Leaf", "Normal",
            "Water")
# The `WorkSuitability_*` columns, thirteen of them, in the enum spelling the tables use.
WORK_JOBS = ("Collection", "Cool", "Deforest", "EmitFlame", "GenerateElectricity",
             "Handcraft", "Mining", "MonsterFarm", "OilExtraction", "ProductMedicine",
             "Seeding", "Transport", "Watering")
# The technology categories, which are the pak's own `TypeA` for whatever a technology
# grants plus `BuildObject` for the 217 that place a structure. Written out for the same
# reason ELEMENTS is: a schema is a contract and must not change shape because a data file
# was regenerated. `progression.categories()` reads the live set and the two are asserted
# equal in the tests, so a patch that adds one fails loudly instead of silently offering
# the model a goal the data no longer serves.
TECH_CATEGORIES = ("Accessory", "Ammo", "Armor", "BuildObject", "CaptureItemModifier",
                   "Consume", "Essential", "Food", "Glider", "Material",
                   "SpecialWeapon", "Weapon")


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
                        "The resource the question names. Empty when it names none. "
                        "For base_site this is what the base is FOR, and it may hold "
                        "several - \"a base for ore and coal\" is two."
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
                        "not resolved to a Pal name. Use it for a tower LEADER, who is "
                        "a person and not a species: \"how do I beat Victor\" is "
                        "target=\"Victor\" with pals empty. Null when none is named."
                    ),
                },
                "max_player_level": {
                    "type": ["integer", "null"],
                    "description": (
                        "Only return results safe at or below this player level. Set it "
                        "only when the player states a level; otherwise null."
                    ),
                },
                "pal_elements": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(ELEMENTS)},
                    "description": (
                        "pal_search only: the element being asked FOR. The player says "
                        "Electric for Electricity, Grass for Leaf, Ground for Earth. "
                        "Empty when no element is described."
                    ),
                },
                "pal_work": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(WORK_JOBS)},
                    "description": (
                        "pal_search only: the base job being asked for. Kindling is "
                        "EmitFlame, Planting is Seeding, Handiwork is Handcraft, "
                        "Gathering is Collection, Lumbering is Deforest, Farming is "
                        "MonsterFarm. Empty when no job is described."
                    ),
                },
                "pal_level": {
                    "type": ["integer", "null"],
                    "description": (
                        "pal_search only: the level of the PAL being looked for, never "
                        "the player's - \"an electric pal that is level 60\" is 60. "
                        "Null when none is stated. For a MOUNT question use "
                        "mount_unlock_level instead: a saddle is gated on the player."
                    ),
                },
                "mount_query": {
                    "type": "boolean",
                    "description": (
                        "pal_search only: true when the question is about Pals you can "
                        "RIDE - mounts, saddles, \"what can I fly on\". False otherwise."
                    ),
                },
                "mount_medium": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["land", "water"]},
                    "description": (
                        "pal_search only. \"land\" covers flying AND ground - the game "
                        "gives them one speed and has no separate flight speed. "
                        "\"water\" is swimming. LEAVE EMPTY when the question names no "
                        "medium, which ranks each Pal by whichever of its two speeds is "
                        "higher. An array rather than a nullable enum because strict "
                        "tool use has no clean form for the latter."
                    ),
                },
                "mount_unlock_level": {
                    "type": ["integer", "null"],
                    "description": (
                        "pal_search only: the PLAYER's level in a mount question - "
                        "\"the fastest mount I can get at level 60\" is 60. A saddle is "
                        "a technology unlocked at a player level, so this is NOT the "
                        "Pal's level. Null otherwise."
                    ),
                },
                "mount_unowned": {
                    "type": "boolean",
                    "description": (
                        "pal_search only: true when the question asks which mounts the "
                        "player does NOT have yet. False otherwise."
                    ),
                },
                "own_base": {
                    "type": "boolean",
                    "description": (
                        "base_rating only: true when the question is about the player's "
                        "OWN base - \"how good is my base\". False when it is about "
                        "where they are standing - \"rate this spot\". The coordinate "
                        "itself comes from the save, never from you."
                    ),
                },
                "tech_ancient_only": {
                    "type": "boolean",
                    "description": (
                        "tech_next only: true when the question is specifically about "
                        "ANCIENT technology points (the pool from tower bosses) - "
                        "\"what should I spend my ancient points on\". False for an "
                        "ordinary \"what should I research\", which considers both pools."
                    ),
                },
                "tech_goal": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(TECH_CATEGORIES)},
                    "description": (
                        "tech_next only: what KIND of technology the player asked for. "
                        "BuildObject is anything you place in a base - a furnace, a "
                        "breeding farm, a bed. Essential covers Pal gear and saddles. "
                        "SpecialWeapon is Pal Spheres. Empty when the question just asks "
                        "what to research next, which is the common case."
                    ),
                },
            },
            "required": ["query_class", "pals", "resources", "items_named", "target",
                         "max_player_level", "pal_elements", "pal_work", "pal_level",
                         "mount_query", "mount_medium", "mount_unlock_level",
                         "mount_unowned", "tech_goal", "tech_ancient_only",
                         "own_base"],
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
    # The boss arrives through the `pals` enum when it IS a Pal, and through the verbatim
    # `target` slot when it is not - see the fallback in `unpack`. Adding the eight tower
    # leaders to the Pal enum was the alternative and it is worse twice over: Victor is
    # not a species and would become selectable as one by every other class, and the enum
    # is the single largest thing in the request (~2,630 tokens) that this whole module
    # exists to stop duplicating.
    #
    # Either way the dispatcher does the resolving, which is what lets an unnamed tower
    # ("the first tower") decline honestly instead of being guessed at: counters.plan
    # raises on a target it has no row for.
    "plan_counters": ("boss",),
    # Nothing positional: its three slots are all its own and all optional, so `unpack`
    # fills them by name below rather than by zipping the shared entity lists. The whole
    # point of the class is that it names no entity.
    "find_pals_by_attribute": (),
    # Same, and more so: this one names no entity AND needs no argument at all. "What
    # should I research next" is a complete question, so an empty call is correct rather
    # than a model failing to fill a slot.
    "suggest_next_unlock": (),
    # Its resources come as a LIST, not a first-slot scalar - the whole class is about one
    # circle reaching several - so the zip below would truncate it. Filled by name.
    "suggest_base_sites": (),
    # No slots at all. The question IS the query, and the dispatcher uses the utterance -
    # so there is nothing for a model to fill in and nothing for it to get wrong.
    "lookup_corpus": (),
    # One boolean, filled by name below. Which PLACE is not the model's to choose - the
    # dispatcher resolves it from the save, because a coordinate a model produced would
    # be the one thing this project never lets one produce.
    "rate_base_site": (),
    # Nothing at all: it names no place, no entity and takes no options. The one class
    # here that answers with no player state whatsoever.
    "explain_base_criteria": (),
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

    # A counter target that is not a Pal - one of the eight tower leaders. Only when the
    # resolved slots produced nothing, so a question naming both a leader and a species
    # keeps the species: the enum is constrained and the free string is not, and between
    # a validated value and an unvalidated one the validated one wins.
    if tool == "plan_counters" and not out.get("boss") and args.get("target"):
        out["boss"] = args["target"]

    if tool == "find_pals_by_attribute":
        # One element and one job, not several. Two elements would mean an intersection
        # nobody asked for ("a fire AND water Pal" is four species) and the arrays exist
        # only because strict tool use has no clean optional enum - see the module
        # docstring. The dispatcher declines when all three come back empty.
        if args.get("pal_elements"):
            out["element"] = args["pal_elements"][0]
        if args.get("pal_work"):
            out["work"] = args["pal_work"][0]
        if args.get("pal_level") is not None:
            out["level"] = args["pal_level"]

        # Any of the four slots marks a mount question, not just the boolean: a model
        # that sets `mount_medium` and forgets `mount_query` would otherwise get an
        # ordinary attribute search carrying a medium nothing could use.
        medium = list(args.get("mount_medium") or [])
        unlock = args.get("mount_unlock_level")
        unowned = bool(args.get("mount_unowned"))
        if args.get("mount_query") or medium or unlock is not None or unowned:
            out["mount"] = True
            # One medium. Two would be an intersection - a mount that is fastest on land
            # AND in water is one ranking, not two - and the array exists only because
            # strict tool use has no clean optional enum.
            if medium:
                out["medium"] = medium[0]
            if unlock is not None:
                out["player_level"] = unlock
            if unowned:
                out["unowned"] = True
            # A mount question's level is the PLAYER's. If the model put it in the Pal
            # slot anyway - the two are one word apart in the utterance - move it rather
            # than filtering wild spawn bands by a saddle level, which would answer a
            # mount question with Pals that happen to spawn at 60.
            if "level" in out and unlock is None:
                out["player_level"] = out.pop("level")
    if tool == "rate_base_site":
        if args.get("own_base"):
            out["own_base"] = True
        # Reuses the shared `resources` slot rather than adding one: for base_site it is
        # what the base is for, and for base_rating it is the same thing narrowed to a
        # place the player already named. Same meaning, same enum, no extra tokens.
        if args.get("resources"):
            out["resources"] = list(args["resources"])

    if tool == "suggest_base_sites":
        out["resources"] = list(args.get("resources") or [])

    if tool == "suggest_next_unlock":
        # One goal, not several, for the same reason pal_search takes one element: two
        # categories would be an intersection nobody asked for, and the array exists only
        # because strict tool use has no clean optional enum.
        if args.get("tech_goal"):
            out["goal"] = args["tech_goal"][0]
        if args.get("tech_ancient_only"):
            out["currency"] = "ancient"
        # `max_player_level` is reused rather than given a mount-style twin, and here that
        # is right rather than lazy: its existing meaning is already the PLAYER's level,
        # which is exactly what a `LevelCap` gates on. The mount case needed its own slot
        # because the competing reading was the PAL's level; no such reading exists for a
        # technology.
        if args.get("max_player_level") is not None:
            out["player_level"] = args["max_player_level"]

    if tool == "find_resource_nodes" and args.get("max_player_level") is not None:
        out["max_player_level"] = args["max_player_level"]
    if tool == "evaluate_counter" and args.get("target") is not None:
        out["target"] = args["target"]
    return tool, out
