"""Execution layer — deterministic answers over local data.

Pure functions over the in-memory knowledge base. No I/O, no model calls, fully
unit-testable. This is where every factual value in a Tier 1 card originates.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .knowledge import (Dropper, KnowledgeBase, Mount, PalAttributes, PalDrop,
                        Ranch, ResourceNode, SpawnArea)


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


# ------------------------------------------------------------------ pal info


@dataclass(frozen=True)
class PalInfoResult:
    """Everything already known about one Pal, gathered rather than computed.

    **The class the 2026-08-11 session asked for most and the product did not have.**
    Nine of forty-one utterances were *"tell me about X"*, *"who is X"*, *"what level is
    X"* - and the damage was not that they declined. Seven of them were **answered by the
    wrong class**: a location card for "tell me about Shroomer", a Tier 2 counter plan for
    "who is Victor". A wrong-class answer is worse than a decline, because it looks like
    an answer.

    Nothing here is new data. Every field is already loaded for some other card, which is
    why this is Tier 1 and why it costs nothing to be complete.
    """
    pal: str
    known: bool
    elements: tuple[str, ...]
    bands: tuple[tuple[int, int], ...]
    level_kind: str | None
    in_overworld: bool
    # Job -> level, already trimmed to what a card can show, highest first.
    work: list[tuple[str, int]]
    mount: Mount | None
    ranch: Ranch | None
    ranch_source: str
    drops: int                  # how many distinct items, for a "and it drops N things"
    spawn_areas: int

    @property
    def rideable(self) -> bool:
        return self.mount is not None


def get_pal_info(kb: KnowledgeBase, pal: str, work_limit: int = 3) -> PalInfoResult:
    """A summary of one Pal, from the datasets already in memory.

    `known=False` only when the name is in the lexicon and in none of the datasets, which
    is a real state - the Terraria collab Pals and a few quest actors have a name and
    nothing else - and it is different from the Pal not existing.
    """
    attrs = kb.attributes.get(pal)
    areas = [a for a in kb.spawns if a.pal == pal]
    drops = kb.pal_drops.get(pal, [])
    work = sorted(((j, v) for j, v in (attrs.work if attrs else {}).items() if v),
                  key=lambda kv: (-kv[1], kv[0]))[:work_limit]
    return PalInfoResult(
        pal=pal,
        known=attrs is not None or bool(areas) or bool(drops),
        elements=attrs.elements if attrs else (),
        bands=attrs.bands if attrs else (),
        level_kind=attrs.level_kind if attrs else None,
        in_overworld=pal not in kb.pals_without_areas,
        work=work,
        mount=kb.mounts.get(pal),
        ranch=kb.ranch.get(pal),
        ranch_source=kb.ranch_source,
        drops=len(drops),
        spawn_areas=len(areas),
    )


# ------------------------------------------------------- search by attribute
#
# **The first class that describes an entity instead of naming one.** Every other tool
# here takes a Pal or a resource the player said out loud and returns facts about it;
# this one takes a description - electric, level 60, good at mining - and returns which
# Pals match. STATUS calls that the largest functional gap in the product, and it is a
# gap in the class inventory rather than a bug: Q1-Q7 are all "I know what I want, tell
# me about it" and never "tell me what I want."
#
# It is still Tier 1. Nothing is generated and nothing is judged: this selects rows from
# tables and orders them by a number the game states.


@dataclass(frozen=True)
class AttributeMatch:
    """One Pal that matched, with the values it matched on."""
    pal: str
    elements: tuple[str, ...]
    # Level in the job that was asked for, or None when no job was. Not the Pal's best
    # job - the question was about mining, and printing a Handiwork score beside it
    # would answer a question nobody asked.
    work_level: int | None
    # The band this Pal is met at: the one containing the requested level, or - when
    # nothing contained it - the nearest one. `level_gap` says which.
    band: tuple[int, int] | None
    # How far the band is from the requested level. 0 means it contains it. Non-zero
    # only ever appears when NOTHING matched exactly, and the card says so.
    level_gap: int
    best_work: str | None
    # Populated only when the query was about mounts. `speed` is in the medium that was
    # asked for, or the Pal's better one when none was.
    mount: Mount | None = None
    speed: int | None = None
    # Which medium `speed` came from, for the no-medium case where the card must not let
    # a swimmer's number read as a land speed.
    speed_medium: str | None = None

    def band_label(self) -> str:
        if self.band is None:
            return "no wild spawn"
        lo, hi = self.band
        return f"lvl {lo}" if lo == hi else f"lvl {lo}-{hi}"


@dataclass(frozen=True)
class AttributeResult:
    element: str | None
    work: str | None
    level: int | None
    matches: list[AttributeMatch]
    total_available: int
    # The game's own word for `work` ("EmitFlame" -> "Kindling"), carried so the card
    # never has to print a pak enum and never has to hold a mapping of its own.
    work_label: str | None = None
    # The mount half of the query, all None/False when it was not one.
    mounts_only: bool = False
    medium: str | None = None           # "land" | "water" | None for either
    # The PLAYER's level, from the utterance. Distinct from `level`, which is the Pal's -
    # see STATUS's 2026-08-11 decision and its amendment.
    player_level: int | None = None
    # Mounts the player-level filter had to skip because no technology row unlocks their
    # saddle. Two of 108. Reported rather than silently dropped: their availability is
    # unknown, not disproved.
    unlock_unknown: int = 0
    # True when the query asked for what the player does NOT own. Requires the roster.
    unowned_only: bool = False
    # False when the roster was never read. `unowned_only` is unanswerable without it,
    # and "you own none of these" is a claim about a set nobody looked at.
    roster_known: bool = True
    # False when no Pal's band contained the requested level and the nearest ones were
    # returned instead. The card MUST say so: "the closest thing to an electric Pal at
    # 60" and "an electric Pal at 60" are different answers, and the second is a claim.
    level_exact: bool = True
    # Pals the level filter could not consider because the overworld never places them.
    # Reported rather than silently dropped - a tower boss has no wild level, and
    # "excluded" and "did not match" are different facts.
    without_a_band: int = 0

    @property
    def filtered(self) -> bool:
        return any(f is not None for f in (self.element, self.work, self.level))


def _gap(attrs: PalAttributes, level: int) -> tuple[int, tuple[int, int] | None]:
    """(distance to the nearest band, that band). `(0, band)` when one contains `level`."""
    best: tuple[int, tuple[int, int] | None] = (10_000, None)
    for lo, hi in attrs.bands:
        d = 0 if lo <= level <= hi else (lo - level if level < lo else level - hi)
        if d < best[0]:
            best = (d, (lo, hi))
    return best


def find_pals_by_attribute(
    kb: KnowledgeBase,
    element: str | None = None,
    work: str | None = None,
    level: int | None = None,
    limit: int = 5,
    *,
    mounts_only: bool = False,
    medium: str | None = None,
    player_level: int | None = None,
    unowned_only: bool = False,
    owned: frozenset[str] | None = None,
) -> AttributeResult:
    """Which Pals match a description. At least one filter is required.

    **"Level" is the PAL's level, always** - decided 2026-08-11 and recorded in STATUS.
    The deciding fact is that the cards already speak this way: a spawn card prints
    "Anubis, lvl 68-72", which is the Pal, so a query where "level 60" meant the player
    would make one word mean two things on the same card. It also keeps this a fact
    rather than a judgement - filtering by player level needs a "how far above your level
    can you cope" constant, and this project already has one uncalibrated difficulty rule
    it has not paid off.

    Matching a level is **band containment**, and when nothing contains it the nearest
    bands are returned with `level_exact=False` rather than an empty card. That case is
    common and not an edge: Feybreak places most species at 80, so the wild levels are
    lumpy and there is genuinely no electric Pal at exactly 60. "Nothing at 60, here is
    what is nearest" is the useful true answer; "no results" is the useless one.

    **`player_level` is the other meaning of "level", and it exists only for mounts.**
    STATUS's 2026-08-11 decision that level always means the Pal's is amended rather than
    overturned: a saddle unlocks at a player level the game *states*, so this is the case
    the decision's own reasoning allows - it rejected player level because filtering by it
    needed an uncalibrated "how far above your level can you cope" constant, and a saddle
    gate needs none. The two arrive in separate arguments so neither can be mistaken for
    the other, and a card prints them with different words.
    """
    rows = [a for a in kb.attributes.values()
            if (element is None or element in a.elements)
            and (work is None or a.work.get(work, 0) > 0)]

    unlock_unknown = 0
    roster_known = owned is not None
    if mounts_only:
        rows = [a for a in rows if a.name in kb.mounts]
        if medium == "water":
            rows = [a for a in rows if kb.mounts[a.name].swim]
        if player_level is not None:
            # Counted before filtering: a mount whose saddle has no technology row is not
            # "too high a level", it is unknown, and the card says so rather than letting
            # the omission read as a considered exclusion.
            unlock_unknown = sum(1 for a in rows
                                 if kb.mounts[a.name].unlock_level is None)
            rows = [a for a in rows if kb.mounts[a.name].available_at(player_level)]
        if unowned_only and roster_known:
            # `owned` is character ids, lower-cased, from saves.owned_species - the same
            # set the counter card filters against.
            rows = [a for a in rows
                    if kb.mounts[a.name].character_id.lower() not in owned]

    total = len(rows)

    exact = True
    without_a_band = 0
    if level is not None:
        without_a_band = sum(1 for a in rows if not a.bands)
        placed = [a for a in rows if a.bands]
        contained = [a for a in placed if a.spawns_at(level)]
        # Widen only when nothing matched, never to pad a thin result: mixing exact
        # matches with near ones would make the card's own claim untrue for some rows
        # and true for others, with nothing to tell them apart.
        exact = bool(contained)
        rows = contained if contained else placed

    gaps = {a.name: (_gap(a, level) if level is not None and a.bands else (0, None))
            for a in rows}

    def speed_of(a: PalAttributes) -> int:
        m = kb.mounts.get(a.name)
        return (m.speed(medium) or 0) if m else 0

    def sort_key(a: PalAttributes):
        # Speed leads for a mount query - "the fastest mount I can get" is a question
        # about that number and nothing else, exactly as a job level leads below.
        #
        # Then job level, then highest Pal level, which is STATUS's decision and carries
        # its caveat: highest is a proxy for strongest and nothing more, so the CARD must
        # not imply a ranking the data does not carry. Name breaks every tie, so two runs
        # on one save produce the same list.
        return (gaps[a.name][0],
                -speed_of(a) if mounts_only else 0,
                -a.work.get(work, 0) if work else 0,
                -(a.level_max or 0),
                a.name)

    rows.sort(key=sort_key)

    def match(a: PalAttributes) -> AttributeMatch:
        m = kb.mounts.get(a.name) if mounts_only else None
        return AttributeMatch(
            pal=a.name, elements=a.elements,
            work_level=a.work.get(work) if work else None,
            band=(gaps[a.name][1] if level is not None
                  else (a.bands[-1] if a.bands else None)),
            level_gap=gaps[a.name][0],
            best_work=a.best_work,
            mount=m,
            speed=m.speed(medium) if m else None,
            # For an explicit medium this is just that medium. With none asked for it is
            # whichever of the two won, and the card needs it: printing 2520 beside a
            # Pal without saying it is a swim speed reads as a land speed.
            speed_medium=(medium or m.fastest_medium) if m else None,
        )

    return AttributeResult(
        element=element, work=work, level=level,
        matches=[match(a) for a in rows[:limit]],
        total_available=total,
        work_label=kb.job_label(work) if work else None,
        level_exact=exact,
        without_a_band=without_a_band,
        mounts_only=mounts_only,
        medium=medium,
        player_level=player_level,
        unlock_unknown=unlock_unknown,
        unowned_only=unowned_only,
        roster_known=roster_known,
    )
