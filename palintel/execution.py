"""Execution layer — deterministic answers over local data.

Pure functions over the in-memory knowledge base. No I/O, no model calls, fully
unit-testable. This is where every factual value in a Tier 1 card originates.
"""
from __future__ import annotations

from dataclasses import dataclass

from .knowledge import KnowledgeBase, ResourceNode


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
