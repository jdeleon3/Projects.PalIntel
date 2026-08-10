"""Size run-to-run variance of the router's entity accuracy (assumption A5).

The 86.1% in commit b4ddb09 is one sample of a nondeterministic model, and P32 was
already observed flipping between runs. Tuning against a point estimate fits noise, so
this measures how much the headline number moves before anything is changed.

Only *boundary* prompts are re-run. A prompt whose expected entity is the rank-1
candidate at score 1.0 on a clean transcript ("where can I find Suzaku?") has no
judgement left in it and cannot plausibly flip; spending requests there buys nothing.
Variance in the headline comes entirely from prompts where the router had a real call to
make, so the budget goes there and the rest are treated as fixed.

Breadth over depth: with a fixed budget, one repeat each of many boundary prompts
estimates the *aggregate* flip rate - which is what the headline's variance depends on -
better than many repeats of a few, because the prompts are not interchangeable.

    python tools/eval/router_variance.py [--reps 1] [--model claude-opus-5]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from palintel.knowledge import KnowledgeBase  # noqa: E402
from palintel.routing_anthropic import (ClaudeRouter,  # noqa: E402
                                        pal_spawn_schema)
from _router_tools import eval_tool_schemas  # noqa: E402
from palintel.tools import Decline  # noqa: E402

EVAL = REPO / "data" / "stt_eval"

# The boundary set, with why each one is in it. Ranks are the expected entity's position
# in the corrector's candidate list (tools/eval/rank_entities.py).
BOUNDARY = {
    "P06": "miss - rank 8, 'Piranha' is an English word",
    "P15": "miss - rank 3, outranked by Sparkit",
    "P23": "miss - rank 1 @ 0.77, over-conservative decline",
    "P32": "miss - known flake, routed correctly on retry",
    "P07": "hit  - recovered from rank 69 on sentence context",
    "P11": "hit  - 'healthsphere', expected entity outside top 3",
    "P25": "hit  - 'Nakhlim', expected entity outside top 3",
    "P35": "hit  - 'an over spot', expected entity outside top 3",
    "P19": "hit  - rank 1 but only 0.63",
    "P21": "hit  - rank 1 at 0.75, just under the old 0.78 threshold",
    "P24": "hit  - rank 1 at 0.67",
    "P37": "hit  - rank 3 at 0.67, beaten by two Pals",
}

# Outcome recorded in commit b4ddb09. Every utterance not listed here was a hit.
BASELINE_MISSES = {"P06", "P15", "P23", "P28", "P32"}

# P28 is excluded deliberately: Omascul is absent from the candidate list entirely, so
# there is nothing for the router to choose and the miss is deterministic. The three
# no-entity prompts are excluded too - their best candidate scores 0.62, far from
# anything the router would name.


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--reps", type=int, default=1, help="repeats per boundary prompt")
    args = ap.parse_args()

    rows = json.loads((EVAL / args.condition / "results.json").read_text(encoding="utf-8"))
    selected = [r for r in rows if r["id"] in BOUNDARY]
    if len(selected) != len(BOUNDARY):
        sys.exit(f"expected {len(BOUNDARY)} boundary prompts, found {len(selected)}")

    kb = KnowledgeBase.load("1.0.2")
    entities = set(kb.lexicon.canonical_names)
    # timeout_s=None keeps the SDK default: this measures run-to-run variance, and a
    # request cut off at the runtime bound would enter the tally as a decline.
    router = ClaudeRouter(kb.lexicon, {n.resource for n in kb.nodes}, model=args.model,
                          extra_tools=[pal_spawn_schema(kb.lexicon.pals()),
                                       *eval_tool_schemas(kb.lexicon.pals())],
                          timeout_s=None)

    print(f"model={args.model}  boundary prompts={len(selected)}  reps={args.reps}  "
          f"requests={len(selected) * args.reps}\n")

    outcomes: dict[str, list[bool]] = defaultdict(list)
    detail = []
    t0 = time.perf_counter()
    for rep in range(args.reps):
        for r in selected:
            heard, expected = r["boosted_text"], set(r["expected"])
            call = router.route(heard, kb.lexicon.rank(heard))
            if isinstance(call, Decline):
                got, kind = set(), "decline"
            else:
                got = {v for v in call.args.values()
                       if isinstance(v, str) and v in entities}
                kind = call.name
            hit = bool(got & expected)
            outcomes[r["id"]].append(hit)
            u = router.last_usage
            detail.append({"id": r["id"], "rep": rep, "hit": hit, "kind": kind,
                           "got": sorted(got), "expected": sorted(expected),
                           "in_tok": u.input if u else 0, "out_tok": u.output if u else 0,
                           "cached_tok": u.cache_read if u else 0,
                           "write_tok": u.cache_write if u else 0,
                           "usd": round(u.usd, 5) if u else 0.0})
            was = "hit" if r["id"] not in BASELINE_MISSES else "miss"
            now = "hit" if hit else "miss"
            flag = "FLIP " if was != now else "same "
            print(f"  {flag} {r['id']} rep{rep}  baseline={was:<4} now={now:<4} "
                  f"got={sorted(got) or kind}")

    print("\n" + "=" * 68)
    flips = agree = 0
    for pid, obs in outcomes.items():
        base = pid not in BASELINE_MISSES
        f = sum(1 for o in obs if o != base)
        flips += f
        agree += len(obs) - f
        mark = "UNSTABLE" if 0 < f else ""
        print(f"  {pid}  baseline={'hit' if base else 'miss':<4} "
              f"runs={''.join('H' if o else 'm' for o in obs):<6} "
              f"flips={f}  {BOUNDARY[pid]}  {mark}")

    n = flips + agree
    print("=" * 68)
    print(f"  boundary observations   {n}")
    print(f"  disagreed with baseline {flips} = {flips / n * 100:.1f}%")
    # Only these prompts can move; the other 24 utterances are treated as fixed. So the
    # headline swing implied by this flip rate is flips-per-run out of 36 utterances.
    per_run = flips / args.reps
    print(f"  implied swing           +/-{per_run:.1f} utterances per run "
          f"= {per_run / 36 * 100:.1f} points on the 36-utterance headline")
    spend = sum(d["usd"] for d in detail)
    out_tok = sum(d["out_tok"] for d in detail)
    print(f"  cost                    ${spend:.2f} over {len(detail)} requests"
          f"  ({time.perf_counter() - t0:.0f}s)")
    print(f"  tokens                  {sum(d['write_tok'] for d in detail)} written, "
          f"{sum(d['cached_tok'] for d in detail)} read from cache, "
          f"{sum(d['in_tok'] for d in detail)} uncached in, {out_tok} out")
    print("=" * 68)

    out = EVAL / args.condition / f"variance_{args.model}.json"
    out.write_text(json.dumps(detail, indent=2), encoding="utf-8")
    print(f"detail -> {out}")


if __name__ == "__main__":
    main()
