"""Execution layer — deterministic answers over local data.

Pure functions over the in-memory knowledge base. No I/O, no model calls, fully
unit-testable. This is where every factual value in a Tier 1 card originates.
"""
from __future__ import annotations

from dataclasses import dataclass

from .knowledge import KnowledgeBase, ResourceNode, SpawnArea


@dataclass(frozen=True)
class ResourceResult:
    resource: str
    nodes: list[ResourceNode]
    near: tuple[float, float] | None
    level_filtered: bool
    total_available: int


def find_resource_nodes(
    kb: KnowledgeBase,
    resource: str,
    max_player_level: int | None = None,
    near: tuple[float, float] | None = None,
    limit: int = 3,
) -> ResourceResult:
    """Locate resource clusters, optionally level-gated and sorted by proximity.

    `near` and `max_player_level` are injected by the dispatcher from live save state,
    never parsed out of the utterance - "nearest" has to resolve against where the
    player actually is.
    """
    matches = [n for n in kb.nodes if n.resource == resource]
    total = len(matches)

    level_filtered = False
    if max_player_level is not None:
        # min_player_level is not yet derived, so nodes carrying None are NOT dropped:
        # silently hiding every node because a field is unpopulated would be worse than
        # returning ungated results. Whether gating actually applied is reported so the
        # card can say so rather than implying a guarantee it cannot make.
        gated = [n for n in matches
                 if n.min_player_level is None or n.min_player_level <= max_player_level]
        level_filtered = any(n.min_player_level is not None for n in matches)
        matches = gated

    if near is not None:
        matches.sort(key=lambda n: n.distance_to(*near))
    else:
        # Without a reference point, biggest cluster first is the most useful default:
        # more deposits per trip.
        matches.sort(key=lambda n: (-n.node_count, n.node_id))

    return ResourceResult(
        resource=resource,
        nodes=matches[:limit],
        near=near,
        level_filtered=level_filtered,
        total_available=total,
    )


# Kinds a caller may ask for, in the order a bare "where do I find X" falls back through.
# Normal spawns are what the question means; the rest exist so a Pal that ONLY appears as
# a field alpha (Necromus, Paladius) gets its real location instead of a "not found".
SPAWN_KINDS = ("normal", "alpha", "predator")


@dataclass(frozen=True)
class SpawnResult:
    pal: str
    areas: list[SpawnArea]
    near: tuple[float, float] | None
    # What was actually returned, which is not always what was asked for - see
    # `kind_substituted`.
    kind: str | None
    # True when no `normal` area exists and an alpha or predator one was returned instead.
    # The card has to say so: "the only Chillet here is a level 55 alpha" is a different
    # warning from a coordinate, and a player who walks in expecting a level 12 encounter
    # finds out the hard way.
    kind_substituted: bool
    total_available: int
    # The Pal is real and the game simply never places it in the overworld: a tower boss,
    # a raid boss, a dungeon-only species. Distinct from having no matching area, because
    # "keep looking" is wrong advice and "it isn't out there" is right.
    in_overworld: bool


def find_pal_spawns(
    kb: KnowledgeBase,
    pal: str,
    kind: str | None = None,
    near: tuple[float, float] | None = None,
    night: bool | None = None,
    limit: int = 3,
) -> SpawnResult:
    """Locate where a Pal spawns, optionally filtered by kind and time of day.

    As with `find_resource_nodes`, `near` is injected by the dispatcher from live save
    state rather than parsed from the utterance.
    """
    mine = [a for a in kb.spawns if a.pal == pal]

    wanted = kind if kind in SPAWN_KINDS else None
    substituted = False
    if wanted is not None:
        matches = [a for a in mine if a.kind == wanted]
    else:
        # Fall through the kinds in order rather than mixing them. A list interleaving a
        # level 12 field spawn with a level 55 alpha is not one answer to one question.
        for k in SPAWN_KINDS:
            matches = [a for a in mine if a.kind == k]
            if matches:
                substituted = k != "normal"
                wanted = k
                break
        else:
            matches = []

    if night is not None:
        matches = [a for a in matches if a.night_only == night]

    total = len(matches)

    if near is not None:
        matches.sort(key=lambda a: a.distance_to(*near))
    else:
        # Without a reference point, the best spot is where you are most likely to meet
        # one: points times the chance each rolls this species. Sorting by raw point
        # count instead would rank Mimog's 139 sheets of 2%-weight filler above a
        # dedicated spawner, which is exactly backwards.
        matches.sort(key=lambda a: (-a.density, a.area_id))

    return SpawnResult(
        pal=pal,
        areas=matches[:limit],
        near=near,
        kind=wanted,
        kind_substituted=substituted,
        total_available=total,
        in_overworld=pal not in kb.pals_without_areas,
    )
