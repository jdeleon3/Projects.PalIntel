"""Compare scored router runs against each other, and against a held-out split.

Two questions this answers that a single run's summary cannot.

**Did a change generalise, or did it fit the eval?** Tuning round 1 was measured on
batches 0-1 and those are now the *training* prompts in every meaningful sense - the
decline wording was rewritten after reading their failures. Any batch recorded later is
held out by construction, so the split below is the honest test.

**Is the difference between two models real?** Runs share prompts, so the comparison is
paired and McNemar's exact test applies. Comparing two independent proportions on n=190
would need a far larger gap to reach the same confidence, and would be the wrong test.

    python tools/eval/compare_routers.py --condition quiet \\
        --models gemini-3.6-flash claude-haiku-4-5 --tuned-on 0 1
"""
from __future__ import annotations

import argparse
import json
from math import comb
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "data" / "stt_eval"


def mcnemar(a_wins: int, b_wins: int) -> float:
    """Exact two-sided binomial test on the discordant pairs."""
    n = a_wins + b_wins
    if not n:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(a_wins, b_wins) + 1))
    return min(1.0, tail / 2 ** n * 2)


def exact(r: dict) -> bool:
    return set(r["got"]) == set(r["expected"])


def wrong(r: dict) -> bool:
    """Any entity the speaker did not say - including one slot of a correct pair."""
    return bool(set(r["got"]) - set(r["expected"]))


def summarise(rows: list[dict], label: str) -> None:
    n = len(rows)
    if not n:
        print(f"  {label:<22} (no prompts)")
        return
    e = sum(map(exact, rows))
    w = sum(map(wrong, rows))
    d = sum(1 for r in rows if r["kind"] == "decline")
    ne = [r for r in rows if not r["expected"]]
    halluc = sum(1 for r in ne if r["got"])
    print(f"  {label:<22}{e:>4}/{n:<4}{e / n * 100:>6.1f}% exact   "
          f"wrong {w:>3} ({w / n * 100:>4.1f}%)   declined {d / n * 100:>5.1f}%"
          f"   no-entity {len(ne) - halluc}/{len(ne)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--tuned-on", nargs="*", type=int, default=[0, 1],
                    help="batches the prompts/config were tuned against; everything "
                         "else is treated as held out")
    args = ap.parse_args()

    src = EVAL / args.condition
    batch = {p["id"]: p.get("batch", 0) for p in
             json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]}
    diff = {p["id"]: p.get("difficulty", "?") for p in
            json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]}

    runs: dict[str, dict[str, dict]] = {}
    for m in args.models:
        f = src / f"router_{m.replace(':', '-')}.json"
        if not f.exists():
            raise SystemExit(f"no scored run at {f}")
        runs[m] = {r["id"]: r for r in json.loads(f.read_text(encoding="utf-8"))
                   if r["group"] == "utterance"}

    ids = sorted(set.intersection(*(set(v) for v in runs.values())))
    if not ids:
        raise SystemExit("the runs share no prompts")
    tuned = [i for i in ids if batch.get(i) in args.tuned_on]
    held = [i for i in ids if batch.get(i) not in args.tuned_on]

    print(f"condition={args.condition}  shared prompts={len(ids)}  "
          f"tuned-on={len(tuned)}  held-out={len(held)}\n")

    for m in args.models:
        print(m)
        summarise([runs[m][i] for i in ids], "all")
        if tuned and held:
            summarise([runs[m][i] for i in tuned], "tuned-on batches")
            summarise([runs[m][i] for i in held], "HELD OUT")
        print()

    if len(args.models) == 2:
        a, b = args.models
        for label, sel in (("all", ids), ("held out", held)):
            if not sel:
                continue
            aw = [i for i in sel if exact(runs[a][i]) and not exact(runs[b][i])]
            bw = [i for i in sel if exact(runs[b][i]) and not exact(runs[a][i])]
            p = mcnemar(len(aw), len(bw))
            print(f"paired, {label:<9} {a} wins {len(aw):>3}, {b} wins {len(bw):>3}"
                  f"   exact McNemar p = {p:.4f}"
                  f"{'  significant' if p < 0.05 else ''}")

    print()
    bands = sorted({diff.get(i, '?') for i in ids})
    print(f"{'difficulty':12s}{'n':>4}  " + "".join(f"{m[:16]:>18}" for m in args.models))
    for d in bands:
        sel = [i for i in ids if diff.get(i) == d]
        row = f"{d:12s}{len(sel):>4}  "
        for m in args.models:
            e = sum(exact(runs[m][i]) for i in sel)
            w = sum(wrong(runs[m][i]) for i in sel)
            row += f"{e / len(sel) * 100:>13.0f}% w{w:<3}"
        print(row)


if __name__ == "__main__":
    main()
