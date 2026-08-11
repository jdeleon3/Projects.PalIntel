"""Build the breeding dataset: one rank per tribe, plus the exception table.

Input : data/raw/pal_monster_parameter.json  (PakExtract.exe tables - CombiRank)
        data/raw/pal_combi_unique.json       (PakExtract.exe tables - exceptions)
        data/<version>/lexicon.json
Output: data/<version>/breeding.json

[ADR-0008](../../Docs/adr/0008-breeding-graph-derivation.md) is **Provisional**: it bets
that breeding is deterministic from a single per-Pal rank plus an override table, and it
requires 100% agreement against >= 100 independently-known combinations before that bet is
accepted. This script builds the inputs to that check. It does **not** derive edges and it
does not decide whether the model holds - see `tools/eval/score_breeding.py`.

**Three things the ADR did not know, all found by reading the tables.**

*The model is tribe-level, not character-level.* `DT_PalCombiUnique` keys on
`EPalTribeID`, while `DT_PalMonsterParameter` is keyed by CharacterID and carries a row per
boss, raid, predator, quest and oil-rig copy of the same creature - 753 rows for roughly
260 real tribes. Those copies share their tribe's rank, so ranking over CharacterIDs makes
almost every rank look ambiguous when it is not. Collapsing to tribes first is what makes
"the Pal whose rank is nearest the average" a well-formed question.

*`CombiDuplicatePriority` exists.* Ranks genuinely collide between distinct tribes, so
"nearest the average" is under-specified on its own, and the game ships an explicit
tiebreak field. Recorded here; which direction it breaks is measured, not assumed.

*Variants are underivable in principle.* 88 of the 121 distinct exception children are
variant forms - Chillet Ignis (`LazyDragon_Electric`), Mau Cryst (`Bastet_Ice`),
Baphomet Noct. They exist **only** as exception rows, so no rank arithmetic reaches them
and a rank-only model does not merely get them wrong, it cannot express them.

The exception count is deliberately reported two ways. ADR-0008 says a "handful" confirms
the rank model and "hundreds" means the rule is a table in disguise - a test on the raw
count, which is 258 and would fail it. Against the pair space the same table is 0.19%, and
that is the number that speaks to whether the rank rule is carrying the work.

Usage: python tools/ingest/build_breeding.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Rows the game itself excludes from breeding. IgnoreCombi is the game's own flag; the
# rank sentinel and the boss flags are belt and braces, because IgnoreCombi alone leaves
# 261 BOSS_/Quest_ rows in the set and every one of them collides with its own tribe.
UNBREEDABLE_RANK = 9999


def _enum(value: str) -> str:
    """`EPalTribeID::LazyDragon` -> `LazyDragon`."""
    return value.rsplit("::", 1)[-1] if isinstance(value, str) else value


def load_tribes(rows: dict) -> tuple[dict, list[str]]:
    """Collapse CharacterID rows to one entry per breedable tribe.

    A tribe's copies must agree about its rank, or "the rank of this tribe" is not a
    thing. Disagreements are returned rather than resolved - silently picking one would
    be inventing the fact the whole dataset is supposed to carry.
    """
    by_tribe: dict[str, dict] = {}
    conflicts: list[str] = []

    for char_id, r in rows.items():
        if r.get("IgnoreCombi") or r.get("CombiRank", UNBREEDABLE_RANK) == UNBREEDABLE_RANK:
            continue
        if r.get("IsBoss") or r.get("IsTowerBoss") or r.get("IsRaidBoss"):
            continue

        tribe = _enum(r.get("Tribe", ""))
        if not tribe:
            continue
        entry = {
            "tribe": tribe,
            "character_id": char_id,
            "rank": r["CombiRank"],
            "priority": r.get("CombiDuplicatePriority"),
            "zukan_index": r.get("ZukanIndex"),
            "zukan_suffix": r.get("ZukanIndexSuffix") or "",
        }
        prior = by_tribe.get(tribe)
        if prior is None:
            by_tribe[tribe] = entry
            continue
        if prior["rank"] != entry["rank"]:
            conflicts.append(f"{tribe}: {prior['character_id']}={prior['rank']} vs "
                             f"{char_id}={entry['rank']}")
        # Prefer the plainest CharacterID as the tribe's representative: a name with no
        # underscore is the base form, and variants carry a suffix.
        if "_" in prior["character_id"] and "_" not in char_id:
            by_tribe[tribe] = entry

    return by_tribe, conflicts


def load_exceptions(rows: dict) -> list[dict]:
    """The override table, as ordered pairs are NOT meaningful - normalise to a set.

    Two of the 258 rows are gender-qualified and the other 256 are not, so gender is
    carried rather than dropped. Dropping it would silently merge the one pair the game
    distinguishes by it.
    """
    out = []
    for row_id, r in rows.items():
        a, b = _enum(r["ParentTribeA"]), _enum(r["ParentTribeB"])
        ga, gb = _enum(r.get("ParentGenderA", "None")), _enum(r.get("ParentGenderB", "None"))
        # Sort the pair so a lookup never has to try both orders - except when genders
        # differ, where the pairing is (tribe, gender) and sorting on tribe alone would
        # scramble which parent carries which gender.
        if ga == gb and b < a:
            a, b, ga, gb = b, a, gb, ga
        out.append({"row": row_id, "parent_a": a, "parent_b": b,
                    "gender_a": ga, "gender_b": gb,
                    "child_character_id": r["ChildCharacterID"]})
    return out


def build(version: str) -> dict:
    mp = json.loads((RAW / "pal_monster_parameter.json").read_text(encoding="utf-8"))
    mp_rows = (mp[0] if isinstance(mp, list) else mp)["Rows"]
    cu = json.loads((RAW / "pal_combi_unique.json").read_text(encoding="utf-8"))
    cu_rows = (cu[0] if isinstance(cu, list) else cu)["Rows"]
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))

    tribes, conflicts = load_tribes(mp_rows)
    exceptions = load_exceptions(cu_rows)

    # CharacterID -> display name, so the dataset can be joined against anything written
    # by a human. Built from the lexicon rather than re-derived.
    name_of = {}
    for p in lexicon["pals"]:
        for internal in p["internal_ids"]:
            name_of[internal.lower()] = p["canonical"]
    for t in tribes.values():
        t["name"] = name_of.get(t["character_id"].lower())

    ranks = Counter(t["rank"] for t in tribes.values())
    collisions = {rank: sorted(t["tribe"] for t in tribes.values() if t["rank"] == rank)
                  for rank, n in ranks.items() if n > 1}
    # A collision the priority field cannot separate is a genuine ambiguity in the model.
    unresolved = {rank: names for rank, names in collisions.items()
                  if len({tribes[n]["priority"] for n in names}) < len(names)}

    known_ids = {t["character_id"] for t in tribes.values()}
    exception_only = sorted({e["child_character_id"] for e in exceptions
                             if e["child_character_id"] not in known_ids})

    # Which exception children could a rank-only model never reach? Anything that is not
    # a breedable tribe in its own right.
    by_child = defaultdict(list)
    for e in exceptions:
        by_child[e["child_character_id"]].append(e)

    n = len(tribes)
    pair_space = n * (n + 1) // 2

    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "DT_PalMonsterParameter + DT_PalCombiUnique",
        "model_status": "PROVISIONAL - ADR-0008 gate not yet run; see "
                        "tools/eval/score_breeding.py",
        "source_note": "CombiRank is per tribe, not per CharacterID. Boss, raid, "
                       "predator, quest and oil-rig rows share their tribe's rank and "
                       "are excluded here; including them makes 251 of 260 rank "
                       "collisions look real when they are one creature counted twice.",
        "stats": {
            "monster_parameter_rows": len(mp_rows),
            "breedable_tribes": n,
            "distinct_ranks": len(ranks),
            "rank_collisions": len(collisions),
            "collisions_unresolved_by_priority": len(unresolved),
            "tribe_rank_conflicts": len(conflicts),
            "exception_rows": len(exceptions),
            "exception_distinct_children": len(by_child),
            "exception_children_not_breedable_tribes": len(exception_only),
            "gender_qualified_exceptions": sum(
                1 for e in exceptions if e["gender_a"] != "None"),
            "pair_space": pair_space,
            "exceptions_as_share_of_pairs": round(len(exceptions) / pair_space, 6),
        },
        "tribes": sorted(tribes.values(), key=lambda t: t["rank"]),
        "exceptions": sorted(exceptions, key=lambda e: (e["parent_a"], e["parent_b"])),
        "rank_collisions": {str(k): v for k, v in sorted(collisions.items())},
        "collisions_unresolved_by_priority": {str(k): v
                                              for k, v in sorted(unresolved.items())},
        "tribe_rank_conflicts": conflicts,
        "exception_children_not_breedable_tribes": exception_only,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    for needed in (RAW / "pal_monster_parameter.json", RAW / "pal_combi_unique.json"):
        if not needed.exists():
            sys.exit(f"Missing {needed}\n  "
                     f"dotnet run --project tools/extract/PakExtract -- tables")

    data = build(args.version)
    dest = REPO / "data" / args.version / "breeding.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"breeding -> {dest}")
    print(f"  monster parameter rows   {s['monster_parameter_rows']}")
    print(f"  breedable tribes         {s['breedable_tribes']}"
          f"  ({s['distinct_ranks']} distinct ranks)")
    print(f"  rank collisions          {s['rank_collisions']}"
          f"  ({s['collisions_unresolved_by_priority']} not separable by priority)")
    print(f"  exception rows           {s['exception_rows']}"
          f"  -> {s['exception_distinct_children']} distinct children"
          f"  ({s['exceptions_as_share_of_pairs']:.2%} of the pair space)")
    print(f"  exception children that are not breedable tribes  "
          f"{s['exception_children_not_breedable_tribes']}")
    if data["tribe_rank_conflicts"]:
        print("  TRIBE RANK CONFLICTS - the tribe-level premise does not hold:")
        for c in data["tribe_rank_conflicts"][:10]:
            print(f"    {c}")
    if data["collisions_unresolved_by_priority"]:
        print("  collisions with no tiebreak:")
        for rank, names in list(data["collisions_unresolved_by_priority"].items())[:5]:
            print(f"    rank {rank}: {', '.join(names)}")


if __name__ == "__main__":
    main()
