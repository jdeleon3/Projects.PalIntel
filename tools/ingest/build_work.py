"""Build the work-suitability dataset — the third axis of Pal search by attribute.

Input : data/raw/pal_monster_parameter.json              (WorkSuitability_* levels)
        data/raw/tables/en_DT_UI_Common_Text_Common.json (the job names, in English)
        data/<version>/lexicon.json                      (internal id -> display name)
Output: data/<version>/work.json

**Nothing here is derived.** The levels are integer columns on the Pal row and the job
labels are the game's own UI strings, so "Anubis is Mining 4" is extracted fact in the
same sense a coordinate is. That matters because the attribute search this feeds is the
first class that *ranks* Pals, and a ranking built on an inference would be a claim
wearing a fact's clothes.

Two things this deliberately does NOT do:

* **No 14th job.** The table carries thirteen `WorkSuitability_*` columns and the UI
  names thirteen. `Mining_Stone`, `_Copper`, `_Iron` and `_Platinum` also have UI keys,
  but their text is the untranslated `en Text` placeholder and no Pal row has a column
  for them - they are ore-tier gates on the Mining job, not jobs. Counting them would
  have published four suitabilities no Pal can have.
* **No "best at" ranking beyond the number.** `BestWorkSuitability` is carried through
  as the game states it, and a level is a level: a Mining 4 Pal out-mines a Mining 3 one
  and that is the entire claim. It says nothing about whether the Pal is *good*, which
  is a judgement this project has no calibrated way to make.

**Read `en_DT_UI_Common_Text_Common.json`, never a bare `DT_UI_Common_Text_Common`.**
Two tables share a filename across `L10N/en` and the base path, and STATUS records the
session where a single export filename let the base one win and shipped item names in
Japanese. Only the `en_` export is accepted here, and its absence is a hard error rather
than a fallback.

Usage: python tools/ingest/build_work.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

PREFIX = "WorkSuitability_"
# UI keys for the ore tiers, which look like jobs and are not. Excluded by name so a
# future patch adding a real thirteenth job is not silently swallowed by a pattern.
NOT_A_JOB = ("Mining_Stone", "Mining_Copper", "Mining_Iron", "Mining_Platinum")
# The untranslated marker the game leaves in rows with no English string. Treated as
# missing rather than as a label - "en Text" on a card would be a data leak.
PLACEHOLDER = "en Text"


def job_names() -> dict[str, str]:
    """`Mining` -> `Mining`, `EmitFlame` -> `Kindling`. From the game, not from a map."""
    path = RAW / "tables" / "en_DT_UI_Common_Text_Common.json"
    if not path.exists():
        sys.exit(f"Missing {path}\n  "
                 f"dotnet run --project tools/extract/PakExtract -- tables\n"
                 f"  The en_ prefix is required: the base-path table of the same name "
                 f"is not English, and reading it shipped Japanese item names once.")
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = (doc[0] if isinstance(doc, list) else doc)
    rows = rows.get("Rows", rows)

    out = {}
    for key, row in rows.items():
        if not key.startswith("COMMON_WORK_SUITABILITY_"):
            continue
        enum = key[len("COMMON_WORK_SUITABILITY_"):]
        if enum in NOT_A_JOB:
            continue
        label = ((row or {}).get("TextData") or {}).get("SourceString", "").strip()
        if label and label != PLACEHOLDER:
            out[enum] = label
    return out


def build(version: str) -> dict:
    mp = json.loads((RAW / "pal_monster_parameter.json").read_text(encoding="utf-8"))
    rows = (mp[0] if isinstance(mp, list) else mp)["Rows"]
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    name_of = {i.lower(): p["canonical"]
               for p in lexicon["pals"] for i in p["internal_ids"]}

    labels = job_names()
    # The columns actually present on a Pal row, which is the authority on what a job is.
    # Intersected with the UI names rather than trusted alone, so a column with no player
    # -facing label cannot reach a card as a raw enum.
    columns = sorted({k[len(PREFIX):] for r in rows.values() for k in r
                      if k.startswith(PREFIX)})
    jobs = [c for c in columns if c in labels]
    unlabelled = [c for c in columns if c not in labels]

    entries, unnamed = [], []
    for cid, r in rows.items():
        levels = {j: int(r.get(PREFIX + j) or 0) for j in jobs}
        if not any(levels.values()):
            # A Pal suited to nothing is a real fact, but it can never match an attribute
            # search and carrying 400 empty rows would triple the file for no query.
            # Counted in stats so the omission is visible.
            continue
        name = name_of.get(cid.lower())
        if not name:
            # Summons, quest actors, boss rows. They have work columns and no player
            # -facing name, so a card could not print them.
            unnamed.append(cid)
            continue
        best = (r.get("BestWorkSuitability") or "").rsplit("::", 1)[-1]
        entries.append({
            "character_id": cid,
            "name": name,
            "levels": {j: v for j, v in levels.items() if v},
            "best": best if best in labels else None,
        })

    # One display name can carry several internal ids (Horus and Horus_Oilrig). Keep the
    # highest level seen per job under one name: the player owns "a Faleris", not a
    # variant id, and taking whichever row happened to come last was arbitrary.
    merged: dict[str, dict] = {}
    for e in entries:
        prev = merged.get(e["name"])
        if prev is None:
            merged[e["name"]] = dict(e)
            continue
        for job, level in e["levels"].items():
            prev["levels"][job] = max(prev["levels"].get(job, 0), level)
        prev["best"] = prev["best"] or e["best"]

    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "pak",
        "source": "DT_PalMonsterParameter WorkSuitability_* + BestWorkSuitability; "
                  "job labels from en_DT_UI_Common_Text_Common COMMON_WORK_SUITABILITY_*",
        "extraction_note": "Nothing here is derived. Levels are integer columns and "
                           "labels are the game's own UI strings, so a work level is "
                           "extracted fact in the same sense a coordinate is.",
        "ranking_note": "A level is a level. Mining 4 out-mines Mining 3 and that is the "
                        "whole claim - it says nothing about whether the Pal is good, "
                        "which this project has no calibrated way to judge.",
        "tier_note": "COMMON_WORK_SUITABILITY_Mining_Stone/_Copper/_Iron/_Platinum have "
                     "UI keys and are NOT jobs: their text is the untranslated 'en Text' "
                     "placeholder and no Pal row carries a column for them. They gate "
                     "which ore a Mining level can work, and counting them would have "
                     "published four suitabilities no Pal can have.",
        "jobs": {j: labels[j] for j in jobs},
        "stats": {
            "jobs": len(jobs),
            "pals_with_any_suitability": len(merged),
            "rows_read": len(rows),
            "rows_with_no_suitability": sum(
                1 for r in rows.values()
                if not any(int(r.get(PREFIX + j) or 0) for j in jobs)),
            "rows_without_a_name": len(unnamed),
            "columns_without_a_ui_label": unlabelled,
            "by_job": {labels[j]: sum(1 for e in merged.values() if j in e["levels"])
                       for j in jobs},
        },
        "entries": sorted(merged.values(), key=lambda e: e["name"]),
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
    if data["stats"]["columns_without_a_ui_label"]:
        # A work column the UI does not name is either a new job whose label moved, or a
        # column that is not a job at all. Either way it must not be guessed at.
        sys.exit("work columns with no UI label: "
                 + ", ".join(data["stats"]["columns_without_a_ui_label"])
                 + "\n  Add them to NOT_A_JOB if they are ore tiers, or find their "
                   "COMMON_WORK_SUITABILITY_ key. Nothing was written.")

    dest = REPO / "data" / args.version / "work.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"work -> {dest}")
    print(f"  jobs               {s['jobs']}  ({', '.join(data['jobs'].values())})")
    print(f"  pals with any      {s['pals_with_any_suitability']}"
          f"  of {s['rows_read']} rows read")
    print(f"  no suitability     {s['rows_with_no_suitability']}"
          f"   unnamed rows dropped {s['rows_without_a_name']}")
    top = sorted(s["by_job"].items(), key=lambda kv: -kv[1])[:4]
    print(f"  widest jobs        " + ", ".join(f"{k} {v}" for k, v in top))


if __name__ == "__main__":
    main()
