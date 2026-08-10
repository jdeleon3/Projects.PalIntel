"""Spatial clustering shared by the resource-node and Pal-spawn ingests.

Both datasets answer the same shape of question - "where do I go for X" - from a scatter
of individual actor placements, and both need the same guarantee: a cluster must be
somewhere a player can stand, not a statistical region. Lifted verbatim out of
build_resource_nodes.py when build_pal_spawns.py needed it, so the two cannot drift.
"""
from __future__ import annotations

import math
from collections import defaultdict


def cluster(points: list[dict], radius: float) -> list[list[dict]]:
    """Leader clustering: every member lies within `radius` of the cluster seed.

    Single-link clustering was tried first and chains badly - deposits strung along a
    cliff face merge into one 171-member "cluster" spanning a whole region, which is
    not a place a player can go. Seeding bounds cluster diameter at 2*radius by
    construction, so a cluster is always somewhere you can stand and see the group.

    Seeds are chosen densest-first so the natural centre of a group wins, rather than
    an arbitrary edge deposit.
    """
    buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        buckets[(int(p["map_x"] // radius), int(p["map_y"] // radius))].append(i)

    def neighbours(i: int) -> list[int]:
        bx, by = int(points[i]["map_x"] // radius), int(points[i]["map_y"] // radius)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for k in buckets.get((bx + dx, by + dy), ()):
                    if math.dist((points[i]["map_x"], points[i]["map_y"]),
                                 (points[k]["map_x"], points[k]["map_y"])) <= radius:
                        out.append(k)
        return out

    density = {i: len(neighbours(i)) for i in range(len(points))}
    taken = [False] * len(points)
    clusters = []

    for i in sorted(density, key=lambda k: -density[k]):
        if taken[i]:
            continue
        group = [k for k in neighbours(i) if not taken[k]]
        for k in group:
            taken[k] = True
        clusters.append([points[k] for k in group])
    return clusters


def spread(group: list[dict], cx: float, cy: float) -> float:
    """Greatest distance from the reported coordinate to any member point."""
    return max(math.dist((p["map_x"], p["map_y"]), (cx, cy)) for p in group)


def anchor(group: list[dict]) -> dict:
    """The real placement nearest the group's centroid.

    Never report the centroid itself: it can land in a lake or off a cliff, and every
    coordinate handed to a player has to be a place something actually is.
    """
    cx = sum(p["map_x"] for p in group) / len(group)
    cy = sum(p["map_y"] for p in group) / len(group)
    return min(group, key=lambda p: math.dist((p["map_x"], p["map_y"]), (cx, cy)))
