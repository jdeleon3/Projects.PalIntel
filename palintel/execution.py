"""Execution layer — deterministic answers over local data.

Pure functions over the in-memory knowledge base. No I/O, no model calls, fully
unit-testable. This is where every factual value in a Tier 1 card originates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .knowledge import (Dropper, KnowledgeBase, PalDrop, Ranch, ResourceNode,
                        SpawnArea)


@dataclass(frozen=True)
class ResourceResult:
    resource: str
    nodes: list[ResourceNode]
    near: tuple[float, float] | None
    level_filtered: bool
    total_available: int
    # The other way to get it. Populated for 11 of 18 resources; empty is the normal
    # case for stone, wood and the World Tree materials, which nothing drops.
    droppers: list[Dropper] = field(default_factory=list)


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
        droppers=kb.droppers.get(resource, []),
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
    # What it produces on a ranch, when it is one of the 29 that produce anything. None
    # is the common case - most Pals cannot be ranched at all.
    ranch: Ranch | None = None
    # Attribution for the line above, because unlike everything else on this card those
    # facts are not extracted from the game files. See ADR-0014's amendment.
    ranch_source: str = ""


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

    # Density first, distance second - and knowing where the player is changes the
    # tiebreak, not the definition of a good spot.
    #
    # Sorting by distance alone shipped through Phase 2 and was wrong in play. Asked for
    # Cattiva it returned a 1-point area 191 units away and never mentioned the 60-point
    # one; the reported nearest spots were places you could stand and see nothing. Raw
    # spawn count is not the fix either: two of Cattiva's biggest areas carry a 3%
    # encounter share, so 27 spawners mostly roll something else - the exact thing
    # `encounter_share` exists to warn about. Points times share is expected encounters,
    # which is the question being asked.
    #
    # This also removes an inconsistency worth naming: the no-position branch already
    # ranked by density, so "best" silently meant two different things depending on
    # whether the save could be read.
    if near is not None:
        matches.sort(key=lambda a: (-a.density, a.distance_to(*near), a.area_id))
    else:
        matches.sort(key=lambda a: (-a.density, a.area_id))

    return SpawnResult(
        pal=pal,
        areas=matches[:limit],
        near=near,
        kind=wanted,
        kind_substituted=substituted,
        total_available=total,
        in_overworld=pal not in kb.pals_without_areas,
        ranch=kb.ranch.get(pal),
        ranch_source=kb.ranch_source,
    )


@dataclass(frozen=True)
class DropsResult:
    pal: str
    # Ordinary drops first, alpha-only after - see `drops_card`. Both lists together are
    # everything the Pal yields; neither is a truncation.
    ordinary: list[PalDrop]
    alpha_only: list[PalDrop]
    # Endgame level bands, kept apart because they are a different creature in practice:
    # a level 80 Chillet drops 30-50 Ancient Relics and an ordinary one drops leather.
    high_level: list[PalDrop]
    # The Pal is real and drops nothing at all, which is a fact rather than missing data.
    known: bool

    @property
    def total(self) -> int:
        return len(self.ordinary) + len(self.alpha_only) + len(self.high_level)


def find_pal_drops(kb: KnowledgeBase, pal: str) -> DropsResult:
    """What a Pal yields when defeated or captured.

    Split by encounter kind rather than returned flat. Most of what a Vanwyrm drops is
    alpha-only - Ancient Civilization Parts, Precious Plume, a Giant Pal Soul - and a
    player who reads that list and goes hunting ordinary Vanwyrms comes back with a Bone.
    The split is the answer to the question actually being asked.
    """
    drops = kb.pal_drops.get(pal)
    if drops is None:
        # No row at all. Distinct from an empty one: `known=False` means this Pal is not
        # in the drop table, not that it drops nothing.
        return DropsResult(pal=pal, ordinary=[], alpha_only=[], high_level=[],
                           known=False)
    return DropsResult(
        pal=pal,
        ordinary=[d for d in drops if not d.alpha_only and not d.min_level],
        alpha_only=[d for d in drops if d.alpha_only and not d.min_level],
        high_level=[d for d in drops if d.min_level],
        known=True,
    )


@dataclass(frozen=True)
class ItemSourceResult:
    item: str
    ordinary: list[Dropper]
    alpha_only: list[Dropper]
    high_level: list[Dropper]
    known: bool

    @property
    def total(self) -> int:
        return len(self.ordinary) + len(self.alpha_only) + len(self.high_level)


def find_item_source(kb: KnowledgeBase, item: str) -> ItemSourceResult:
    """Which Pals drop a named item.

    The mirror of `find_pal_drops`, split the same three ways and for the same reason: 78
    Pals drop Leather from an ordinary encounter, while Ancient Civilization Parts comes
    only from alphas. A single ranked list would send a player after a field boss without
    saying so.
    """
    sources = kb.item_sources.get(item)
    if sources is None:
        return ItemSourceResult(item=item, ordinary=[], alpha_only=[], high_level=[],
                                known=False)
    return ItemSourceResult(
        item=item,
        ordinary=[d for d in sources if not d.alpha_only and not d.min_level],
        alpha_only=[d for d in sources if d.alpha_only and not d.min_level],
        high_level=[d for d in sources if d.min_level],
        known=True,
    )
