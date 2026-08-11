"""Build the boss dataset for Q5 counters.

Input : data/raw/pal_monster_parameter.json      (IsTowerBoss / IsRaidBoss / IsBoss)
        data/raw/tables/DT_PalRaidBoss_Common.json  (raid levels + element overrides)
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

Three traps, all found by looking rather than by reasoning:

1. **`Boss_Anubis`.** One row in 323 uses different capitalisation, and it is Anubis - a
   Pal the play protocol asks about by name. `startswith("BOSS_")` drops it silently, so
   prefix matching here is case-insensitive.
2. **Raid bosses have body parts.** `RAID_YakushimaBoss002_Hand_Left`, `_Hand_Right` and
   `_Head` are separate character rows. "What counters Moon Lord's left hand" is not a
   question, so they are excluded and counted.
3. **`_2` is the same fight, harder.** Not a second boss. Marked as a tier rather than
   published as a duplicate entry.

Usage: python tools/ingest/build_bosses.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

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
        "gap_note": "CORRECTED 2026-08-11. This previously said no table links a GYM_ "
                    "Pal to its tower. It does: DT_UniqueNPCText carries BOSSNAME_DEMO_"
                    "<REGION>_LEADER and _LEADER_PAL pairs - ZOE/GRIZZBOLT, VICTOR/"
                    "SHADOWBEAK, and six more. The earlier claim came from searching "
                    "DT_UniqueNPC, the NPC definitions, and never DT_UniqueNPCText, the "
                    "text. Pairing the two keys by their shared region prefix is an "
                    "INFERENCE, of the same class as BOSS_ meaning 'the alpha of' - "
                    "strong (8 pairs, no orphans) but derived. Still genuinely absent: "
                    "tower ORDINALS (nothing says Victor's is the 5th), faction names "
                    "like 'PAL Genetic Research Unit', and any tower boss level.",
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
        },
        "entries": sorted(entries, key=lambda e: (e["kind"], e["character_id"])),
        "excluded_body_parts": sorted(excluded_parts),
        "unnamed": sorted(unnamed),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    needed = RAW / "pal_monster_parameter.json"
    if not needed.exists():
        sys.exit(f"Missing {needed}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- tables")

    data = build(args.version)
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
    print(f"  body parts dropped {s['excluded_body_parts']}")
    if s["unnamed"]:
        print(f"  UNNAMED            {s['unnamed']}: {', '.join(data['unnamed'][:6])}")
    if s["no_elements"]:
        print(f"  no elements at all {s['no_elements']} - cannot be countered by type")


if __name__ == "__main__":
    main()
