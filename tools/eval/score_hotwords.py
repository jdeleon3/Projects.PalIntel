"""Re-measure the STT hotword list — Phase 2's carried-forward item.

Phase 1 hoisted the resources to the front of the hint list and recorded the cost: 2 of
60 Pal clips regressed, "as likely noise as signal on that sample, and worth re-measuring
when Phase 2 registers a tool that depends on them". `find_pal_spawns` is that tool.

Two things changed since, and both are reasons the old numbers cannot simply be carried:

  * The resource set went from 5 to 19, so "resources first" now hoists nineteen entries
    ahead of the Pal names instead of five.
  * Ten of those nineteen canonical ids contain underscores - `hexolite_quartz`,
    `ancient_bark` - and the hint list was built from the ids. Whisper is being biased
    toward strings nobody says. `spoken` tests the display names instead.

Scoring is by whether the expected entity clears the floor the FAST PATH tests, which is
the only thing the transcript has to achieve: 0.78 for a resource, 0.85 for a Pal. `top`
is the stricter reading - the expected entity also outranks every other candidate of its
kind - because clearing the bar while something else outranks you is how a confident card
names the wrong thing.

Transcription is cached per variant, so re-scoring after a metric change is free.

Usage:
    python tools/eval/score_hotwords.py                # every variant, cached
    python tools/eval/score_hotwords.py --refresh      # re-transcribe
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.knowledge import KnowledgeBase  # noqa: E402
from palintel.routing import MIN_CONFIDENT, PAL_CONFIDENT  # noqa: E402
from palintel.stt import MAX_HOTWORDS, MODEL  # noqa: E402

EVAL = REPO / "data" / "stt_eval"
CACHE = EVAL / "hotword_variants.json"


def variants(lexicon, display: dict[str, str]) -> dict[str, list[str] | None]:
    """The orderings under test. None means no hints at all."""
    resources = sorted(lexicon.resources())
    pals = sorted(lexicon.pals())
    spoken = [display.get(r, r.replace("_", " ")) for r in resources]
    # Phase 1 hoisted 5 resources; Phase 2 has 19, and 14 of them are things nobody asks
    # for by voice. `core_first` tests whether the Pal regression is caused by the NUMBER
    # of entries displacing the Pal names, rather than by hoisting resources at all.
    core = [r for r in ("ore", "coal", "sulfur", "quartz", "crude_oil") if r in resources]
    rest = [r for r in resources if r not in set(core)]
    return {
        "none": None,
        "sorted": sorted(lexicon.canonical_names),
        "resources_first": resources + pals,
        "spoken_first": spoken + pals,
        "pals_first": pals + spoken,
        "core_first": core + pals + rest,
        # The recorded clips predate the widening, so they exercise only the Phase 1
        # five. Stone, wood and paldium are the resources most likely to be asked for in
        # real play and have no clips at all; this variant measures what hoisting them
        # COSTS, which is the half of the trade the eval set can still answer.
        "common_first": core + [r for r in ("stone", "wood", "paldium_fragment")
                                if r in resources]
                        + pals
                        + [r for r in rest if r not in
                           ("stone", "wood", "paldium_fragment")],
    }


def score(kb: KnowledgeBase, rows: list[dict], transcripts: dict[str, str]) -> dict:
    counts = {k: [0, 0, 0] for k in ("resource", "pal")}   # cleared, top, total
    misses: list[str] = []
    resources = set(kb.lexicon.resources())

    for row in rows:
        expected = row["expected"][0]
        kind = "resource" if expected in resources else "pal"
        floor = MIN_CONFIDENT if kind == "resource" else PAL_CONFIDENT
        counts[kind][2] += 1

        ranked = kb.lexicon.rank(transcripts[row["id"]], limit=25)
        mine = next((c for c in ranked if c.canonical == expected), None)
        if mine is None or mine.score < floor:
            misses.append(f"{row['id']} {expected}: {transcripts[row['id']]!r}")
            continue
        counts[kind][0] += 1
        same_kind = [c for c in ranked if c.kind == mine.kind]
        if same_kind and same_kind[0].canonical == expected:
            counts[kind][1] += 1
    return {"counts": counts, "misses": misses}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--only", help="comma-separated variant names")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    kb = KnowledgeBase.load("1.0.2")
    lex_raw = json.loads((REPO / "data" / "1.0.2" / "lexicon.json")
                         .read_text(encoding="utf-8"))
    display = {r["canonical"]: r.get("display", r["canonical"])
               for r in lex_raw["resources"]}

    audio_dir = EVAL / args.condition
    results = json.loads((audio_dir / "results.json").read_text(encoding="utf-8"))
    records = results.values() if isinstance(results, dict) else results
    # One expected entity only. Multi-entity prompts need a different scoring rule and
    # would quietly change what the columns mean.
    rows = [r for r in records
            if len(r.get("expected", [])) == 1 and (audio_dir / f"{r['id']}.wav").exists()]

    todo = variants(kb.lexicon, display)
    if args.only:
        todo = {k: v for k, v in todo.items() if k in args.only.split(",")}

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    cache.setdefault("model", MODEL)

    missing = [k for k in todo if args.refresh or k not in cache.get("transcripts", {})]
    if missing:
        from palintel.stt import Transcriber
        cache.setdefault("transcripts", {})
        for name in missing:
            hints = todo[name]
            tr = Transcriber(kb.lexicon)
            tr.hotwords = ", ".join(hints[:MAX_HOTWORDS]) if hints else ""
            t0 = time.perf_counter()
            out = {}
            for i, row in enumerate(rows, 1):
                out[row["id"]] = tr.transcribe(audio_dir / f"{row['id']}.wav",
                                               boost=hints is not None)
                if i % 60 == 0:
                    print(f"  {name}: {i}/{len(rows)} "
                          f"({time.perf_counter() - t0:.0f}s)", flush=True)
            cache["transcripts"][name] = out
            print(f"  {name}: done in {time.perf_counter() - t0:.0f}s", flush=True)
            CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")

    n_res = sum(1 for r in rows if r["expected"][0] in set(kb.lexicon.resources()))
    print(f"\n{len(rows)} clips ({n_res} resource, {len(rows) - n_res} pal), "
          f"model {cache['model']}, floors {MIN_CONFIDENT}/{PAL_CONFIDENT}\n")
    print(f"  {'variant':<18}{'resource':>12}{'top':>7}{'pal':>10}{'top':>7}")

    scored = {}
    for name in todo:
        s = score(kb, rows, cache["transcripts"][name])
        scored[name] = s
        (rc, rt, rn), (pc, pt, pn) = s["counts"]["resource"], s["counts"]["pal"]
        print(f"  {name:<18}{f'{rc}/{rn}':>12}{rt:>7}{f'{pc}/{pn}':>10}{pt:>7}")

    if args.verbose:
        for name, s in scored.items():
            print(f"\n{name} misses ({len(s['misses'])}):")
            for m in s["misses"][:20]:
                print(f"   {m}")


if __name__ == "__main__":
    main()
