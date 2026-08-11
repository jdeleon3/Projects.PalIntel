"""Build the boss dataset for Q5 counters.

Input : data/raw/pal_monster_parameter.json      (IsTowerBoss / IsRaidBoss / IsBoss)
        data/raw/tables/DT_PalRaidBoss_Common.json  (raid levels + element overrides)
        data/raw/tables/DT_UniqueNPCText.json       (the eight tower leaders)
        data/<version>/elements.json
        data/<version>/lexicon.json
Output: data/<version>/bosses.json

**Three different things wear the word "boss" and they do not share a provenance.**

| kind | how many | where the facts come from |
|---|---|---|
| tower | 31 `GYM_*` | element from the Pal row; the human/Pal pair from `DT_UniqueNPCText` (see `gap_note`); **still no level** |
| raid | 17 `RAID_*` | `DT_PalRaidBoss_Common` - authoritative PalId, Level, element overrides |
| alpha | 323 `BOSS_*` | element from the Pal row; locations already in `pal_spawns.json` |

**The display name is derived, and this file says so.** No table maps `GYM_ElecPanda` to a
tower or `BOSS_Alpaca` to Melpaca; the name comes from stripping the prefix and joining
the remainder to the base tribe. [CLAUDE.md](../../CLAUDE.md) names this exact inference -
*"a `BOSS_` prefix meaning 'the alpha of'"* - as the kind of derived rule that must be
declared where it is published and measured before it is trusted. `name_derived` is on
every row and the coverage is in `stats`.

**The tower leader join is new, and it is better sourced than the boss names beside it.**
`pal_names_flat.json` states each pair outright - `PAL_NAME_SnowBoss` is
`"Victor & Shadowbeak"` - and `DT_UniqueNPCText` reaches the same nine pairs by an
entirely different route, which `_leaders.validate` checks and this build fails on if
they ever disagree. So the human-to-Pal half is **not** derived.

What *is* derived is the last step: reaching `GYM_BlackGriffon` from the name
"Shadowbeak" goes through the prefix inference above. `leader_derived` marks that, and it
means one step rather than the two an earlier reading of this claimed. The join
deliberately targets the **tier-1 `GYM_` row** and nothing else: `BOSS_BlackGriffon_
BossRush` is the same creature in a different mode and `_2` is the same fight made
harder, so a leader pointing at either would name a fight the player did not ask about.

Four traps, all found by looking rather than by reasoning:

1. **`Boss_Anubis`.** One row in 323 uses different capitalisation, and it is Anubis - a
   Pal the play protocol asks about by name. `startswith("BOSS_")` drops it silently, so
   prefix matching here is case-insensitive.
2. **Raid bosses have body parts.** `RAID_YakushimaBoss002_Hand_Left`, `_Hand_Right` and
   `_Head` are separate character rows. "What counters Moon Lord's left hand" is not a
   question, so they are excluded and counted.
3. **`_2` is the same fight, harder.** Not a second boss. Marked as a tier rather than
   published as a duplicate entry.
4. **A tower Pal's display name resolves to the ALPHA first.** `bosses.json` is sorted
   by (kind, character_id) and consumers index by name, so "Shadowbeak" reaches
   `BOSS_BlackGriffon`, the field alpha - correct for *"where's the alpha Shadowbeak"*
   and wrong for *"how do I beat Victor"*. That is why the leader index published here
   names a character id rather than a Pal name.

Usage: python tools/ingest/build_bosses.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import _leaders

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

PREFIXES = ("gym_", "raid_", "boss_")
# Body parts of a multi-part raid boss, not targets in their own right.
PART = re.compile(r"_(Hand_Left|Hand_Right|Head|Tail|Core)(_\d+)?$")
TIER = re.compile(r"_(\d+)$")
# `BOSS_BlackGriffon_BossRush` is the same creature in the Boss Rush mode, not another
# boss. Recorded as a mode so a card can say which fight it means, and stripped before
# the base-tribe join - otherwise the name simply fails to resolve.
MODE = re.compile(r"_(BossRush|Oilrig|Quest)$", re.IGNORECASE)


def strip_prefix(cid: str) -> str | None:
    """`BOSS_Alpaca` -> `Alpaca`, case-insensitively. None when no prefix applies."""
    low = cid.lower()
    for p in PREFIXES:
        if low.startswith(p):
            return cid[len(p):]
    return None


def build(version: str) -> dict:
    mp = json.loads((RAW / "pal_monster_parameter.json").read_text(encoding="utf-8"))
    rows = (mp[0] if isinstance(mp, list) else mp)["Rows"]
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    name_of = {i.lower(): p["canonical"]
               for p in lexicon["pals"] for i in p["internal_ids"]}

    # Raid levels and element overrides, keyed by the PalId the table points at.
    raid_path = RAW / "tables" / "DT_PalRaidBoss_Common.json"
    raid_info: dict[str, dict] = {}
    if raid_path.exists():
        rb = json.loads(raid_path.read_text(encoding="utf-8"))
        rb = rb[0] if isinstance(rb, list) else rb
        for row in rb.values():
            for info in row.get("InfoList", []):
                pal = (info.get("PalId") or {}).get("Key")
                if pal:
                    raid_info[pal] = {
                        "level": info.get("Level"),
                        "override": [e.rsplit("::", 1)[-1] for e in
                                     (info.get("OverrideInitialElement1"),
                                      info.get("OverrideInitialElement2"))
                                     if e and e.rsplit("::", 1)[-1] != "None"],
                    }

    def enum(v) -> str | None:
        v = (v or "").rsplit("::", 1)[-1]
        return None if v in ("", "None") else v

    entries, excluded_parts, unnamed = [], [], []
    for cid, r in rows.items():
        if r.get("IsTowerBoss"):
            kind = "tower"
        elif r.get("IsRaidBoss"):
            kind = "raid"
        elif r.get("IsBoss"):
            kind = "alpha"
        else:
            continue

        if PART.search(cid):
            excluded_parts.append(cid)
            continue

        base = strip_prefix(cid)
        tier_m = TIER.search(base or "")
        tier = int(tier_m.group(1)) if tier_m else 1
        base_clean = TIER.sub("", base) if base else None
        mode_m = MODE.search(base_clean or "")
        mode = mode_m.group(1) if mode_m else None
        base_clean = MODE.sub("", base_clean) if base_clean else None

        name = name_of.get((base_clean or "").lower()) or name_of.get(cid.lower())
        elements = [e for e in (enum(r.get("ElementType1")), enum(r.get("ElementType2")))
                    if e]
        info = raid_info.get(cid, {})
        if info.get("override"):
            elements = info["override"]

        if not name:
            unnamed.append(cid)
        entries.append({
            "character_id": cid,
            "kind": kind,
            "base_character_id": base_clean,
            # True whenever the name came from the prefix inference rather than from a
            # table naming this row. Consumers must be able to see that.
            "name": name,
            "name_derived": bool(name and base_clean
                                 and cid.lower() != base_clean.lower()),
            "elements": elements,
            "elements_overridden": bool(info.get("override")),
            "tier": tier,
            "mode": mode,
            "level": info.get("level"),
        })

    leaders, leader_problems, unled = attach_leaders(entries)

    by_kind = {k: sum(1 for e in entries if e["kind"] == k)
               for k in ("tower", "raid", "alpha")}
    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "DT_PalMonsterParameter boss flags + DT_PalRaidBoss_Common",
        "name_note": "No table names a GYM_/RAID_/BOSS_ row. Names are DERIVED by "
                     "stripping the prefix and joining the remainder to the base tribe, "
                     "and every row carries name_derived so a card can decline to assert "
                     "one. This is the inference CLAUDE.md flags by name.",
        "gap_note": "CORRECTED TWICE, 2026-08-11. First reading: no table links a GYM_ "
                    "Pal to its tower - wrong, from searching DT_UniqueNPC (the NPC "
                    "definitions) and never DT_UniqueNPCText (the text). Second reading: "
                    "DT_UniqueNPCText's BOSSNAME_DEMO_<REGION>_LEADER/_LEADER_PAL pairs "
                    "link them by an INFERENCE on the key suffix, strong at 8 pairs - "
                    "also short. pal_names_flat.json STATES each pair in one string "
                    "(PAL_NAME_SnowBoss = 'Victor & Shadowbeak'), it is a file this "
                    "project already extracts and builds the lexicon from, and it "
                    "carries NINE - including Zenara & Astralym, which the text table "
                    "has no key for. The two tables agree on all eight they share and "
                    "the build fails if they stop. Still genuinely absent: tower "
                    "ORDINALS (nothing says Victor's is the 5th), faction names like "
                    "'PAL Genetic Research Unit', and any tower boss level.",
        "leader_note": "Each leader points at a character_id, never at a Pal name: this "
                       "file is sorted by (kind, character_id) and a name index reaches "
                       "the field ALPHA first, so 'Shadowbeak' would answer 'how do I "
                       "beat Victor' with the wrong fight. The join targets the tier-1 "
                       "GYM_ row specifically - not _2 (the same fight, harder) and not "
                       "_BossRush (the same creature, another mode). ONE derived step "
                       "remains and leader_derived marks it: the human-to-Pal pairing is "
                       "stated by two independent tables, but reaching the GYM_ row from "
                       "the Pal's name goes through this file's own prefix inference. "
                       "`corroborated` is false only where the cross-check table has no "
                       "key at all, which today is Zenara & Astralym alone.",
        "casing_note": "Boss_Anubis is the single row of 323 not spelled BOSS_. Prefix "
                       "matching is case-insensitive because a case-sensitive filter "
                       "drops exactly one Pal, and it is one the play protocol asks for.",
        "stats": {
            "entries": len(entries),
            **by_kind,
            "names_resolved": sum(1 for e in entries if e["name"]),
            "names_derived": sum(1 for e in entries if e["name_derived"]),
            "unnamed": len(unnamed),
            "raid_with_level": sum(1 for e in entries if e["level"] is not None),
            "element_overridden": sum(1 for e in entries if e["elements_overridden"]),
            "excluded_body_parts": len(excluded_parts),
            "no_elements": sum(1 for e in entries if not e["elements"]),
            "leaders": len(leaders),
            "towers_without_a_leader": len(unled),
        },
        "entries": sorted(entries, key=lambda e: (e["kind"], e["character_id"])),
        # leader (lower-cased) -> the tier-1 tower row it names. The index a counter
        # lookup reads; the human-readable pairing is on the entries themselves.
        "leaders": leaders,
        # Tower rows the join left without a leader, with why. Published rather than
        # silently empty: an unexpected one is the signal that a patch renamed a tower.
        "towers_without_a_leader": unled,
        # Empty on a good build; `main` refuses to write anything when it is not.
        "leaders_unmatched": leader_problems,
        "excluded_body_parts": sorted(excluded_parts),
        "unnamed": sorted(unnamed),
    }


def attach_leaders(entries: list[dict]) -> tuple[list[dict], list[str], list[dict]]:
    """Write `leader`/`region` onto the tier-1 `GYM_` rows, and return the index.

    Returns `(leaders, problems, unled)`. `problems` is non-empty only when the shape of
    the source changed - an orphaned key, two humans claiming one Pal, or a `_LEADER_PAL`
    naming something no tower row matches - and `main` fails the build on it. Half an
    ingested mapping is worse than none: some towers would carry a leader and some would
    not, and a card cannot tell that apart from a tower that genuinely has none.
    """
    pairs = _leaders.parse(RAW)
    problems = _leaders.validate(RAW, pairs)

    # Only the plain tier-1 GYM_ row is a candidate. `mode` excludes Boss Rush, `tier`
    # excludes the harder rematch, and the GYM_ prefix excludes the field alpha that
    # shares the display name - see trap 4.
    towers = {e["name"].lower(): e for e in entries
              if e["kind"] == "tower" and e["name"] and e["tier"] == 1
              and not e["mode"] and e["character_id"].upper().startswith("GYM_")}

    leaders = []
    for lead in pairs:
        row = towers.get(lead.pal.lower())
        if row is None:
            problems.append(f"{lead.leader}'s Pal {lead.pal!r} matches no tier-1 GYM_ row")
            continue
        row["leader"] = lead.leader
        row["region"] = lead.region
        # Two inferences deep, and the flag says so wherever this row is read. The name
        # this join travelled through is itself derived from the character id.
        row["leader_derived"] = True
        row["leader_corroborated"] = lead.corroborated
        leaders.append({
            "leader": lead.leader,
            "pal": lead.pal,
            # How the game itself names the fight, in one string. This is the primary
            # source, not a rendering of the two fields beside it.
            "display": lead.both,
            "region": lead.region,
            "character_id": row["character_id"],
            "elements": row["elements"],
            "corroborated": lead.corroborated,
        })

    claimed = {l["character_id"] for l in leaders}
    # Every tier-1 GYM tower should now have a leader: the name table carries nine pairs
    # and there are nine towers. An orphan means a patch added a tower or renamed one,
    # and `main` stops on it rather than publishing a tower that silently has no human.
    unled = [{
        "character_id": e["character_id"],
        "name": e["name"],
        "expected": False,
    } for e in entries
        # Same filter the join used, so the two cannot disagree about what a tower is.
        # `name` matters: GYM_BlackGriffon_2_Avatar and GYM_MoonQueen_2_Servant are adds
        # summoned during a fight, they resolve to no tribe, and counting them as
        # leaderless towers would fail the build on two rows that are not towers.
        if e["kind"] == "tower" and e["name"] and e["tier"] == 1 and not e["mode"]
        and e["character_id"].upper().startswith("GYM_")
        and e["character_id"] not in claimed]

    # An unexpected orphan means a tower exists that no leader names, which is either a
    # patch adding one or the pairing breaking. Either way it is not a fact to publish
    # quietly.
    problems += [f"tower {u['character_id']} has no leader and is not a known exception"
                 for u in unled if not u["expected"]]
    return leaders, problems, unled


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    needed = RAW / "pal_monster_parameter.json"
    if not needed.exists():
        sys.exit(f"Missing {needed}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- tables")

    data = build(args.version)

    # Fail before writing. A half-ingested leader mapping publishes some towers with a
    # human name and some without, and nothing downstream can tell that apart from a
    # tower that genuinely has none - which is exactly the "well-formed and wrong" shape
    # CLAUDE.md names as this project's failure mode.
    if data["leaders_unmatched"]:
        sys.exit("leader mapping is not the shape it was measured to be:\n  "
                 + "\n  ".join(data["leaders_unmatched"])
                 + "\n\nSee tools/ingest/_leaders.py. Nothing was written.")

    dest = REPO / "data" / args.version / "bosses.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"bosses -> {dest}")
    print(f"  entries            {s['entries']}"
          f"  (tower {s['tower']}, raid {s['raid']}, alpha {s['alpha']})")
    print(f"  names resolved     {s['names_resolved']}"
          f"  of which DERIVED from the prefix: {s['names_derived']}")
    print(f"  raid levels        {s['raid_with_level']}"
          f"   element overrides {s['element_overridden']}")
    print(f"  tower leaders      {s['leaders']}"
          + (f"  ({', '.join(l['leader'] + ' -> ' + l['character_id'] for l in data['leaders'][:3])}"
             f"{', ...' if s['leaders'] > 3 else ''})" if data["leaders"] else
             "  - DT_UniqueNPCText not extracted"))
    for u in data["towers_without_a_leader"]:
        print(f"    no leader:       {u['character_id']} ({u['name']})"
              f"{'  - expected' if u['expected'] else '  - UNEXPECTED'}")
    print(f"  body parts dropped {s['excluded_body_parts']}")
    if s["unnamed"]:
        print(f"  UNNAMED            {s['unnamed']}: {', '.join(data['unnamed'][:6])}")
    if s["no_elements"]:
        print(f"  no elements at all {s['no_elements']} - cannot be countered by type")


if __name__ == "__main__":
    main()
