"""Q6 progression — what to research next, computed from the save and the tech table.

[ADR-0010](../Docs/adr/0010-three-tier-answer-model.md) puts this in **Tier 2**, beside
`counters.py` and for the same reason: the candidate set is computed, and a model may
only order or phrase what the computation produced. `validate` is the guard, built before
any model pass, exactly as the roadmap requires.

## What is a fact here, and what is not

Every field on a candidate is stated by the game: a required level, a cost, a currency, a
prerequisite. What is *advisory* is the order they come back in, and the card says so —
**highest required level first is a proxy for "most advanced" and nothing more.** It is
the same caveat the mount and attribute cards already carry, and it is carried here for
the same reason: the data supports "this unlocks later", not "this is better".

## Three states, not two

A technology the player does not have is in one of three states, and collapsing them
would produce a confidently wrong card:

* **researchable** — level met, prerequisite met, tower met, and affordable now.
* **affordable later** — every gate met except the points.
* **blocked** — a gate is not met, and `Blocker` says which.

## The level problem, and the floor that solves it

`PlayerState.player_level` is permanently `None`: it lives in a `Level.sav` blob whose
decoder is stale for 1.0.2 (see saves.py). So the level gate has nothing to compare
against — which would leave Q6 unable to answer the one question it exists for.

**The unlocked set implies a lower bound.** A technology cannot be researched below its
`required_level`, so a player holding one at level 57 has been at least level 57. That is
a derived claim rather than a reading, and it is declared here, on the card, and in the
result (`level_is_a_floor`):

* it can only **under**-report, never over-report — anything at or below the floor is
  genuinely available, and something between the floor and the player's real level is
  wrongly shown as out of reach;
* an utterance that states a level wins over it outright, because that is a reading and
  this is an inference.

This is the same amendment STATUS records for mounts: *level means the Pal's, except
where the game itself states a player gate*. A `LevelCap` is such a gate.

## Two currencies that must never be added together

`cost` is paid from one of two pools. `ancient` spends the save's `bossTechnologyPoint`
and `technology` spends `TechnologyPoint`, and a card that summed them would tell a
player they can afford something they cannot.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import Enum
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# How a save records a defeated tower, against the `EPalBossType` suffix the technology
# table gates on. **This join is an inference on a key name**, declared in tech.json's
# `tower_join_note` and repeated here because it is where the inference is acted on: the
# pak states `EPalBossType::ForestBoss` and the save states
# `BOSS_BATTLE_NAME_ForestBoss`, and nothing states that they are the same thing. All
# five flags in the reference save matched a valid enum value, which is evidence and not
# proof.
TOWER_FLAG_PREFIX = "BOSS_BATTLE_NAME_"

ANCIENT, TECHNOLOGY = "ancient", "technology"


class ProgressionError(RuntimeError):
    pass


class Blocker(str, Enum):
    """Why a technology is not researchable. One reason per candidate, most fundamental
    first — a level-80 technology behind an unbeaten tower is reported as level, because
    that is the one the player cannot do anything about today."""
    LEVEL = "level"
    PREREQUISITE = "prerequisite"
    TOWER = "tower"
    POINTS = "points"
    NONE = "none"


@dataclass(frozen=True)
class Technology:
    tech_id: str
    name: str
    required_level: int
    cost: int
    currency: str                   # "ancient" | "technology"
    prerequisites: tuple[str, ...]
    requires_tower: str | None
    requires_research: str | None
    category: str
    unlocks: tuple[str, ...]
    # True when no name row resolved and the tech id is standing in for one. 11 of 588.
    # Carried so a card can avoid dressing an internal id up as a product name.
    name_is_id: bool = False


@dataclass(frozen=True)
class Candidate:
    """One technology, with the verdict the set arithmetic reached about it."""
    tech: Technology
    blocked_by: Blocker

    @property
    def researchable(self) -> bool:
        return self.blocked_by is Blocker.NONE

    @property
    def sort_key(self) -> tuple:
        # Researchable first — an unaffordable suggestion is not actionable — then the
        # most advanced thing available, then the cheapest, then the name so two runs on
        # one save produce the same card.
        return (0 if self.researchable else 1,
                -self.tech.required_level, self.tech.cost, self.tech.name)


@dataclass(frozen=True)
class PlayerTech:
    """The save's half of the arithmetic. Every field may be absent, and absent is normal.

    `unlocked` of None means the save was never read, which is a different answer from
    "you have unlocked nothing" all the way to the card — the same distinction
    `PlayerState.owned_species` carries for the counter card.
    """
    unlocked: frozenset[str] | None = None
    points: int | None = None
    ancient_points: int | None = None
    # Tower suffixes (`ForestBoss`), already stripped of the save's key prefix.
    towers_defeated: frozenset[str] | None = None

    @property
    def known(self) -> bool:
        return self.unlocked is not None

    def affords(self, tech: Technology) -> bool:
        """True when the right pool covers the cost, or when the pool was not read.

        Unread points are optimistic on purpose, and it is the safe direction: the card
        says the balance is unknown, and a player who cannot afford something finds out
        instantly in the technology menu. The opposite error hides a valid suggestion
        behind a number nobody looked at.
        """
        pool = self.ancient_points if tech.currency == ANCIENT else self.points
        return pool is None or pool >= tech.cost

    def level_floor(self, by_id: dict[str, Technology]) -> int | None:
        """The lowest the player's level can possibly be, from what they have unlocked.

        A **lower bound**, never the level. See the module docstring: it under-reports
        and never over-reports, which is the direction that cannot produce a card
        claiming something is available when it is not.
        """
        if not self.unlocked:
            return None
        levels = [by_id[t].required_level for t in self.unlocked if t in by_id]
        return max(levels) if levels else None


@lru_cache(maxsize=4)
def load(version: str = "1.0.2") -> dict[str, Technology]:
    """The technology table, by tech id. Cached — it is 588 immutable rows."""
    path = REPO / "data" / version / "tech.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise ProgressionError(
            f"missing dataset: {path} - run tools/ingest/build_tech.py") from e
    return {e["tech_id"]: Technology(
        tech_id=e["tech_id"], name=e["name"],
        required_level=e["required_level"], cost=e["cost"], currency=e["currency"],
        prerequisites=tuple(e["prerequisites"]),
        requires_tower=e["requires_tower"], requires_research=e["requires_research"],
        category=e["category"], unlocks=tuple(e["unlocks"]),
        name_is_id=e["name_source"] == "tech_id",
    ) for e in raw["entries"]}


# How well a phrase must match a technology's name to be acted on.
#
# **High, and it can afford to be**, because this matcher is scoped to a branch rather
# than global. The lexicon ranks against every utterance, which is why 151 item names are
# kept out of it - `Arrow`, `Bone` and `Leather` would pull spurious candidates into
# questions that name no item. Forty-six technologies have single-word names and twelve
# are ordinary English (`Mine`, `Ranch`, `Mill`, `Sword`, `Sign`), so the same argument
# applies with force.
#
# The difference is that this only ever runs on the object of an unlock verb - "how do I
# unlock the X" - so `Mine` cannot be reached by "where can I go mining". A branch-local
# matcher is safe where a global one is not, which is what lets these names be matched at
# all without a 588-value enum on every request.
NAME_MATCH = 0.80

# Words that carry no part of a technology's name and are in the way of matching one.
_LEADING = re.compile(r"^(?:the|a|an|my|some)\s+", re.I)


def find(query: str, version: str = "1.0.2") -> tuple[Technology, float] | None:
    """The technology a phrase names, with how well it matched. None below the floor.

    Returns the score so a caller can report it and a card can be honest about a near
    miss, in the same spirit as the lexicon returning candidates rather than a verdict.
    """
    from .knowledge import squash

    wanted = squash(_LEADING.sub("", query.strip()))
    if not wanted:
        return None
    scored = []
    for tech in load(version).values():
        # Match on the display name, and on the tech id too - eleven names fall back to
        # their id, and "grappling gun" should still find `GrapplingGun`.
        scored.append((tech, round(max(
            SequenceMatcher(None, wanted, squash(tech.name)).ratio(),
            SequenceMatcher(None, wanted, squash(tech.tech_id)).ratio()), 3)))
    scored.sort(key=lambda s: (-s[1], s[0].name))
    best = scored[0]
    if best[1] < NAME_MATCH:
        return None
    # **Two plausible readings and nothing to separate them is a decline**, which is the
    # rule ROUTING_POLICY states for entities and applies here for the same reason: the
    # answer is a card, and a card cannot ask which one you meant. No two technologies
    # share a display name today, so this fires only on a vague phrase - which is exactly
    # when picking the first would be a coin flip.
    if len(scored) > 1 and scored[1][1] >= best[1] - 0.02:
        return None
    return best


@dataclass(frozen=True)
class Requirement:
    """One gate on a technology, and whether this save clears it."""
    name: str
    met: bool | None            # None when the save cannot say
    detail: str


def requirements(tech: Technology, state: PlayerTech,
                 version: str = "1.0.2") -> list[Requirement]:
    """Every stated gate on one technology, checked against the save.

    The same four `_blocker` reads, spelled out one per line instead of collapsed to the
    first failure - because "what do I still need" is a different question from "can I
    research this", and a card that named only the first missing gate would send someone
    to beat a tower without mentioning they are also nine levels short.
    """
    by_id = load(version)
    unlocked = state.unlocked or frozenset()
    level = state.level_floor(by_id)
    pool = state.ancient_points if tech.currency == ANCIENT else state.points

    out = [Requirement(
        name=f"level {tech.required_level}",
        met=None if level is None else tech.required_level <= level,
        detail=("no player level known" if level is None
                else f"you are at least {level}"),
    ), Requirement(
        name=f"{tech.cost} {'ancient ' if tech.currency == ANCIENT else ''}"
             f"technology point{'s' if tech.cost != 1 else ''}",
        met=None if pool is None else pool >= tech.cost,
        detail="balance unknown" if pool is None else f"you have {pool}",
    )]
    for prereq in tech.prerequisites:
        out.append(Requirement(
            name=by_id[prereq].name if prereq in by_id else prereq,
            met=prereq in unlocked,
            detail="an earlier technology in the same chain",
        ))
    if tech.requires_tower:
        beaten = state.towers_defeated
        out.append(Requirement(
            name=f"the {tech.requires_tower} tower defeated",
            met=None if beaten is None else tech.requires_tower in beaten,
            detail=("no save read" if beaten is None
                    else "the game's own name for that fight"),
        ))
    if tech.requires_research:
        # Lab research is a separate system this project cannot read from the save.
        # Neither filtered on nor hidden - naming it is the only honest move.
        out.append(Requirement(
            name=f"lab research {tech.requires_research}",
            met=None, detail="I can't read lab research from the save"))
    return out


def categories(version: str = "1.0.2") -> list[str]:
    """The goal vocabulary, which is the game's own `TypeA` and not a taxonomy we wrote.

    Used to generate the router's enum, the same way the resource enum is generated from
    the lexicon: a goal the router can name and the data cannot serve is a decline
    waiting to happen.
    """
    return sorted({t.category for t in load(version).values()})


def _blocker(tech: Technology, state: PlayerTech, level: int | None,
             by_id: dict[str, Technology]) -> Blocker:
    """Which gate stops this technology, in the order the player can act on them."""
    unlocked = state.unlocked or frozenset()
    if level is not None and tech.required_level > level:
        return Blocker.LEVEL
    if any(p not in unlocked for p in tech.prerequisites):
        return Blocker.PREREQUISITE
    # Only checkable when the save was read. With no flags at all every tower-gated
    # technology would report as blocked, which is a claim about a set nobody inspected.
    if (tech.requires_tower and state.towers_defeated is not None
            and tech.requires_tower not in state.towers_defeated):
        return Blocker.TOWER
    if not state.affords(tech):
        return Blocker.POINTS
    return Blocker.NONE


@dataclass(frozen=True)
class ProgressionResult:
    """Everything the Q6 card needs, and nothing a model contributed."""
    candidates: list[Candidate]
    goal: str | None
    # The level the filter actually used, and whether it was inferred rather than stated.
    level: int | None
    level_is_a_floor: bool
    # False when no save has been read. "Nothing left to research" and "I have not looked
    # at what you have" are different answers.
    save_known: bool
    points: int | None
    ancient_points: int | None
    # Everything not yet unlocked that matched the goal, before the level and gate cuts.
    total_locked: int
    # How many are held back by each gate, so a card can say "and 40 more behind your
    # level" instead of silently returning three rows out of a hundred.
    blocked: dict[Blocker, int]
    # Candidates that additionally need a Lab research the save cannot be checked for.
    # Reported rather than excluded: excluding claims "you cannot", including silently
    # claims "you can", and only naming it is true.
    research_gated: int
    # Towers whose flag the save carries. Empty frozenset means read-and-none; None means
    # not read at all.
    towers_defeated: frozenset[str] | None = None
    # Which point pool was asked about, when one was. None means both were considered.
    currency: str | None = None

    @property
    def researchable(self) -> list[Candidate]:
        return [c for c in self.candidates if c.researchable]


def plan(state: PlayerTech, goal: str | None = None, player_level: int | None = None,
         currency: str | None = None, limit: int = 5,
         version: str = "1.0.2") -> ProgressionResult:
    """The whole Tier 2 answer, computed. No model is consulted at any point.

    `player_level` is what the utterance said, and it wins over the floor derived from
    the unlocked set — a reading beats an inference. With neither, the level gate is not
    applied at all and `level` is None, which the card reports rather than pretending the
    filter ran.

    `currency` narrows to one of the two pools. It exists because *"what should I spend
    my ancient technology points on"* is a stated filter, and the first version of this
    answered it with a list of ordinary-currency technologies — the same silently dropped
    filter the mount work found in *"which dragons can I ride at level 60"*, and the
    reason the repo's general rule is that an unattached filter word means defer.
    """
    by_id = load(version)

    floor = state.level_floor(by_id)
    level = player_level if player_level is not None else floor
    level_is_a_floor = player_level is None and floor is not None

    unlocked = state.unlocked or frozenset()
    rows = [t for t in by_id.values()
            if t.tech_id not in unlocked
            and (goal is None or t.category == goal)
            and (currency is None or t.currency == currency)]

    candidates = [Candidate(tech=t, blocked_by=_blocker(t, state, level, by_id))
                  for t in rows]

    blocked: dict[Blocker, int] = {}
    for c in candidates:
        if not c.researchable:
            blocked[c.blocked_by] = blocked.get(c.blocked_by, 0) + 1

    candidates.sort(key=lambda c: c.sort_key)
    shown = candidates[:limit]
    return ProgressionResult(
        candidates=shown,
        goal=goal,
        level=level,
        level_is_a_floor=level_is_a_floor,
        save_known=state.known,
        points=state.points,
        ancient_points=state.ancient_points,
        total_locked=len(rows),
        blocked=blocked,
        research_gated=sum(1 for c in shown if c.tech.requires_research),
        towers_defeated=state.towers_defeated,
        currency=currency,
    )


def validate(named: list[str], candidates: list[Candidate]
             ) -> tuple[list[Candidate], list[str]]:
    """Keep only what the computation produced. Returns (kept, discarded).

    The same Tier 2 discipline as `counters.validate`, and deliberately the same shape:
    a model may choose among candidates and phrase them, and may not introduce one. A
    technology the player cannot research is indistinguishable from one they can to
    anyone reading the card.

    Matching accepts the display name or the tech id, case-insensitively, because the
    model sees names and the set is keyed by id.
    """
    by_key: dict[str, Candidate] = {}
    for c in candidates:
        by_key[c.tech.tech_id.lower()] = c
        by_key[c.tech.name.lower()] = c

    kept, discarded, seen = [], [], set()
    for raw in named:
        c = by_key.get(raw.strip().lower())
        if c is None:
            discarded.append(raw)
        elif c.tech.tech_id not in seen:
            seen.add(c.tech.tech_id)
            kept.append(c)
    return kept, discarded
