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


def _name_priority(boss: dict) -> tuple:
    """Sort key deciding which row owns a display name when several share one.

    A tier-1 `GYM_` tower wins, then anything else, and within a group the character id
    keeps the order stable so two runs agree. `_2` is the same fight made harder and
    `_BossRush` is another mode, so neither should claim the plain name.
    """
    tower = (boss["kind"] == "tower" and boss.get("tier") == 1 and not boss.get("mode")
             and boss["character_id"].upper().startswith("GYM_"))
    return (0 if tower else 1, boss["character_id"])


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
    # Keyed by BOTH the character id and the display name, because the two callers hand
    # over different things: the fast path resolves a lexicon entity and passes "Anubis",
    # while the dataset is keyed `boss_anubis`. Looking up only one of them silently
    # matched nothing for every query the router could actually produce.
    #
    # Character ids win on collision - they are unique by construction, while a display
    # name can repeat across tiers of the same fight.
    by_boss: dict[str, dict] = {}
    by_cid = {b["character_id"]: b for b in bosses["entries"]}
    # Display names, TOWER FIRST. Measured in play on 2026-08-11: "how do I beat Orserk"
    # and "how do I beat Grisbolt" both came back about the field alpha and the player
    # pressed "wrong Pal" on both.
    #
    # The entries are sorted by (kind, character_id) and `alpha` sorts before `tower`, so
    # a plain setdefault handed every tower Pal's name to its BOSS_ row. **Seven of the
    # nine of those alphas are placed nowhere in the overworld** - Orserk, Faleris,
    # Selyne, Bastigor, Shaolong, Shadowbeak and Astralym have zero alpha areas - so the
    # reading it chose was a fight the player cannot have.
    #
    # The tie-break is free rather than a judgement call: a GYM_ row and its BOSS_ row
    # come from the same tribe and therefore carry the same element, so the ADVICE is
    # identical either way. Only the label changes, from "field alpha" to "Zoe's tower",
    # and the second is what someone naming a tower species means.
    for b in sorted(bosses["entries"], key=_name_priority):
        if b.get("name"):
            by_boss.setdefault(b["name"].lower(), b)
    for b in bosses["entries"]:
        by_boss[b["character_id"].lower()] = b

    # The eight tower leaders, LAST and unconditionally, so they win over anything above.
    # They cannot collide with a Pal name - no species is called Victor - but the order
    # matters for a reason worth stating: each one points at a **character id**, not at
    # the Pal's display name. Going through the name would resolve "Victor" to
    # `BOSS_BlackGriffon`, the field alpha, because the display-name index above takes
    # the first entry in (kind, character_id) order and `alpha` sorts before `tower`.
    # That is the same creature and a completely different fight.
    for lead in bosses.get("leaders", []):
        row = by_cid.get(lead["character_id"])
        if row is None:
            continue
        by_boss[lead["leader"].lower()] = row
        # And the game's own name for the fight, "Victor & Shadowbeak". That string is a
        # Pal in the lexicon - `pal_names_flat.json` lists it under PAL_NAME_SnowBoss -
        # so it is in the routers' Pal enum and a model can pick it. Without this key it
        # would resolve to nothing and decline, which is the one answer it should never
        # give for the most explicit way of naming a tower there is.
        by_boss[lead["display"].lower()] = row
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
    # The human who owns this tower, when it is one of the eight. None for every raid
    # boss, every field alpha, and for Astralym's tower, which has no leader in the text
    # table. Carried so the card can name the fight the way the player does - a player
    # who asked about Victor should not be answered about Shadowbeak alone.
    leader: str | None = None
    # True when reaching this row from the leader went through the derived display name.
    # Separate from `name_derived`, which is about the Pal's name - though today they
    # coincide, because that derived name is exactly the step this describes.
    leader_derived: bool = False
    # True when BOTH tables name this pair. False means only `pal_names_flat.json` has
    # it, which today is Zenara & Astralym alone. Carried because a single-sourced fact
    # is a weaker claim than a double-sourced one and the card is where that shows.
    leader_corroborated: bool = True
    # False when the roster was never read. "You own nothing that works" and "I have not
    # looked at what you own" are different answers, and collapsing them would publish a
    # confident claim about a set nobody inspected.
    roster_known: bool = True


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


def plan(boss_id: str, owned: frozenset[str] | None, limit: int = 5,
         version: str = "1.0.2") -> CounterResult:
    """The whole Tier 2 answer, computed. No model is consulted at any point.

    `owned` of None means the roster has not been read - reading it costs a full
    Level.sav parse, so it is not on the query path. The typing half of the answer does
    not need it: which element beats this boss is a fact about the boss, and it is worth
    saying on its own rather than withholding until a save is loaded.
    """
    matrix, typing, bosses = load(version)
    boss = bosses.get(boss_id.lower())
    if boss is None:
        raise CounterError(f"not a known boss: {boss_id!r}")
    elements = tuple(boss["elements"])
    if not elements:
        # Named the way the player named it, not by whatever key they happened to use.
        # `plan("zenara")` reporting "zenara has no element" reads as nonsense - Zenara
        # is a person - when the fact is about Astralym, the Pal she fights with.
        what = boss.get("name") or boss["character_id"]
        if boss.get("leader"):
            what = f"{boss['leader']}'s tower boss {what}"
        raise CounterError(f"{what} has no element; it cannot be countered by type")
    candidates = candidate_set(boss_id, owned, version) if owned is not None else []
    considered = sum(1 for c in (owned or ())
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
        roster_known=owned is not None,
        leader=boss.get("leader"),
        leader_derived=bool(boss.get("leader_derived")),
        leader_corroborated=bool(boss.get("leader_corroborated", True)),
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
