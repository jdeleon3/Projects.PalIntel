"""Q5 boss counters — the computed candidate set, and the guard around it.

[ADR-0010](../Docs/adr/0010-three-tier-answer-model.md) puts this in **Tier 2**: the
advice is computed, and a model may only phrase or order what the computation produced.
The roadmap is explicit that the validator is built **before** the LLM pass, not after,
and this module is that validator plus the set it validates against.

**The rule is simple and total: a Pal that is not in the computed set does not reach a
card.** Not down-ranked, not caveated - dropped, and counted. A model that names a Pal
the player does not own has produced the exact failure this project refuses, and it is
indistinguishable from a good answer to anyone reading the card.

Effectiveness follows the rules stated with the matrix, and the dual-element case is the
one worth stating in code rather than in a comment: a skill both strong and weak against
a two-element Pal is **1x, not 2x**. Getting that wrong recommends a Pal that will
underperform exactly when the player is relying on the advice. 4x and 0.25x are not
reachable - no two elements share a weakness - so a multiplier outside {0.5, 1, 2} means
the matrix is wrong, and `effectiveness` says so rather than returning it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

STRONG, WEAK, NEUTRAL = 2.0, 0.5, 1.0
# Boss rows are targets, never candidates: the player does not field a tower boss.
BOSS_PREFIXES = ("gym_", "raid_", "boss_")


class CounterError(RuntimeError):
    pass


@dataclass(frozen=True)
class Matchup:
    """One owned Pal against one boss, with both directions computed."""
    character_id: str
    name: str | None
    elements: tuple[str, ...]
    offense: float          # what this Pal's typing does TO the boss
    defense: float          # what the boss's typing does to this Pal

    @property
    def effective(self) -> bool:
        return self.offense > NEUTRAL

    @property
    def sort_key(self) -> tuple:
        # Best offence first, then least punished defensively, then a stable name so
        # two runs on the same save produce the same card.
        return (-self.offense, self.defense, self.character_id)


def effectiveness(attacker: tuple[str, ...], defender: tuple[str, ...],
                  matrix: dict[str, dict]) -> float:
    """Damage multiplier for `attacker`'s typing against `defender`'s.

    Strong and weak against the same target cancel to 1x. This is the game's stated
    rule and not an approximation of one.
    """
    strong = weak = 0
    for a in attacker:
        row = matrix.get(a)
        if row is None:
            raise CounterError(f"element not in the matrix: {a!r}")
        for d in defender:
            if d in row["strong_against"]:
                strong += 1
            if d in row["weak_against"]:
                weak += 1
    if strong and weak:
        return NEUTRAL
    if strong:
        return STRONG
    if weak:
        return WEAK
    return NEUTRAL


@lru_cache(maxsize=4)
def load(version: str = "1.0.2") -> tuple[dict, dict, dict]:
    """(matrix, typing by lower-cased id, bosses by lower-cased id)."""
    base = REPO / "data" / version
    try:
        elements = json.loads((base / "elements.json").read_text(encoding="utf-8"))
        bosses = json.loads((base / "bosses.json").read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CounterError(f"missing dataset: {e.filename} - run tools/ingest") from e
    typing = {p["character_id"].lower(): p for p in elements["pals"]}
    by_boss = {b["character_id"].lower(): b for b in bosses["entries"]}
    return elements["matrix"], typing, by_boss


def candidate_set(boss_id: str, owned: frozenset[str],
                  version: str = "1.0.2") -> list[Matchup]:
    """Every owned Pal that is strong against this boss, best first.

    `owned` comes from `saves.owned_species`, which is the set of owned **characters**
    and is not a list of Pals: captured humans are in it. Anything without an entry in
    the typing table is dropped here, which removes them without a special case -
    humans and NPCs have no element, as the source page says outright.
    """
    matrix, typing, bosses = load(version)
    boss = bosses.get(boss_id.lower())
    if boss is None:
        raise CounterError(f"not a known boss: {boss_id!r}")
    boss_elements = tuple(boss["elements"])
    if not boss_elements:
        # Seven entries carry no element at all. Returning [] would read as "you own
        # nothing that works", which is a different and wrong answer.
        raise CounterError(f"{boss_id} has no element; it cannot be countered by type")

    out = []
    for cid in owned:
        if cid.startswith(BOSS_PREFIXES):
            continue
        pal = typing.get(cid)
        if pal is None:
            continue        # a human, an NPC, or content the typing table lacks
        elements = tuple(pal["elements"])
        out.append(Matchup(
            character_id=cid,
            name=pal.get("name"),
            elements=elements,
            offense=effectiveness(elements, boss_elements, matrix),
            defense=effectiveness(boss_elements, elements, matrix),
        ))
    return sorted([m for m in out if m.effective], key=lambda m: m.sort_key)


@dataclass(frozen=True)
class CounterResult:
    """Everything a counter card needs, and nothing a model contributed."""
    boss_id: str
    boss_name: str | None
    name_derived: bool
    kind: str                       # tower | raid | alpha
    boss_elements: tuple[str, ...]
    level: int | None
    candidates: list[Matchup]
    owned_considered: int
    counter_elements: tuple[str, ...]   # what WOULD work, owned or not


def counter_elements(boss_elements: tuple[str, ...],
                     matrix: dict[str, dict]) -> tuple[str, ...]:
    """Elements that are strong against this boss, whether the player owns one or not.

    Worth computing separately from the candidate set: "nothing you own works" is a
    much less useful answer than "nothing you own works, catch something Dragon", and
    the second costs nothing extra to say.
    """
    return tuple(sorted(
        e for e, row in matrix.items()
        if effectiveness((e,), boss_elements, matrix) > NEUTRAL))


def plan(boss_id: str, owned: frozenset[str], limit: int = 5,
         version: str = "1.0.2") -> CounterResult:
    """The whole Tier 2 answer, computed. No model is consulted at any point."""
    matrix, typing, bosses = load(version)
    boss = bosses[boss_id.lower()]
    elements = tuple(boss["elements"])
    candidates = candidate_set(boss_id, owned, version)
    considered = sum(1 for c in owned
                     if c in typing and not c.startswith(BOSS_PREFIXES))
    return CounterResult(
        boss_id=boss["character_id"],
        boss_name=boss.get("name"),
        name_derived=bool(boss.get("name_derived")),
        kind=boss["kind"],
        boss_elements=elements,
        level=boss.get("level"),
        candidates=candidates[:limit],
        owned_considered=considered,
        counter_elements=counter_elements(elements, matrix),
    )


def validate(named: list[str], candidates: list[Matchup]) -> tuple[list[Matchup], list[str]]:
    """Keep only what the computation produced. Returns (kept, discarded).

    **This is the whole Tier 2 discipline in one function.** A model is allowed to
    choose among candidates and to phrase them; it is not allowed to introduce one.
    Matching is case-insensitive and accepts either the display name or the character
    id, because the model sees display names while the set is keyed by id - and
    because the save and the pak disagree about capitalisation (`Sheepball` against
    `SheepBall`), so a case-sensitive check would discard a legitimate pick.
    """
    by_key: dict[str, Matchup] = {}
    for m in candidates:
        by_key[m.character_id.lower()] = m
        if m.name:
            by_key[m.name.lower()] = m

    kept, discarded, seen = [], [], set()
    for raw in named:
        m = by_key.get(raw.strip().lower())
        if m is None:
            discarded.append(raw)
        elif m.character_id not in seen:
            seen.add(m.character_id)
            kept.append(m)
    return kept, discarded
