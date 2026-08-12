"""Score the fast path on the branch batch — the prompts written FOR the new classes.

`score_fast_path.py` answers "does the fast path steal from Q1 and Q2", which is a
regression question and was answerable before these classes existed. This answers the
question that was not: **does the branch fire on the way the player actually speaks.**

The two are different measurements and neither substitutes for the other. Scoring
counters across the 240 older transcripts claimed nothing and changed nothing - a
perfect score that proved only that the branch does no harm, because there was not one
counter question in the set to claim.

Each prompt in batch -1 carries `expect_branch`, so this is a straight comparison
against what the router did. Four expectations, and the third is the interesting one:

  counter        -> plan_counters
  item_source    -> find_item_source
  ambiguous      -> plan_counters WITH a chained find_pal_spawns; both cue families
                    fired, so answering one is a coin flip on the tier
  drops          -> find_pal_drops

Run against both the clean prompt text and the transcript. A branch that works on the
written sentence and fails on the spoken one is an STT problem, not a routing one, and
that split is otherwise very hard to see from the outside.

Usage: python tools/eval/score_branches.py --condition quiet
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.knowledge import KnowledgeBase          # noqa: E402
from palintel.routing import StubRouter               # noqa: E402
from palintel.tools import Decline                    # noqa: E402

EVAL = REPO / "data" / "stt_eval"
BATCH = -1

EXPECTED_TOOL = {
    "counter": "plan_counters",
    "item_source": "find_item_source",
    "ambiguous": "plan_counters",
    "drops": "find_pal_drops",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    args = ap.parse_args()

    prompts = json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    batch = {p["id"]: p for p in prompts if p.get("batch") == BATCH}
    if not batch:
        sys.exit(f"no batch {BATCH} prompts - run tools/eval/add_branch_batch.py")

    results_path = EVAL / args.condition / "results.json"
    if not results_path.exists():
        sys.exit(f"no {results_path} - run tools/eval/score_stt.py --condition "
                 f"{args.condition}")
    results = json.loads(results_path.read_text(encoding="utf-8"))
    records = results.values() if isinstance(results, dict) else results
    heard = {r["id"]: r.get("boosted_text") or r.get("text") for r in records
             if r.get("id") in batch}

    kb = KnowledgeBase.load("1.0.2")
    bosses = json.loads(
        (REPO / "data" / "1.0.2" / "bosses.json").read_text(encoding="utf-8"))
    counterable = {b["name"].lower() for b in bosses["entries"] if b.get("name")}
    router = StubRouter(kb.lexicon, {n.resource for n in kb.nodes},
                        counters=True, counterable=counterable)

    def route(text: str):
        call = router.route(text, kb.lexicon.rank(text), [])
        if isinstance(call, Decline):
            return "decline", None
        return call.name, (call.then.name if call.then else None)

    tally: Counter = Counter()
    misses: list[tuple] = []
    print(f"batch {BATCH}: {len(batch)} prompts, {len(heard)} transcribed\n")

    for pid, p in sorted(batch.items()):
        # `expect_path` says which half of the router is supposed to answer. The fast
        # path cannot claim item_source at all, and several counter phrasings put the
        # named Pal in the ATTACKER position and are deliberately left to the model.
        # Scoring those as fast-path misses measures the wrong thing - they are working.
        want = (EXPECTED_TOOL.get(p["expect_branch"])
                if p.get("expect_path", "fast") == "fast" else "decline")
        for kind, text in (("written", p["text"]), ("spoken", heard.get(pid))):
            if not text:
                tally["not transcribed"] += 1
                continue
            got, chained = route(text)
            ok = got == want
            # The ambiguous rows want BOTH answers. Claiming only the counter half is a
            # partial pass, and worth separating: it means the chain did not fire, not
            # that the branch failed.
            if ok and p["expect_branch"] == "ambiguous" and chained != "find_pal_spawns":
                ok = False
                tally[f"{kind}: counter only, no chain"] += 1
                misses.append((pid, kind, p["expect_branch"], text, got, chained))
                continue
            label = "deferred to model (as designed)" if want == "decline" else "hit"
            tally[f"{kind}: {label if ok else 'MISS'}"] += 1
            if not ok:
                misses.append((pid, kind, p["expect_branch"], text, got, chained))

    for k, n in sorted(tally.items()):
        print(f"  {k:<34} {n}")

    if misses:
        print(f"\n{len(misses)} misses:")
        for pid, kind, branch, text, got, chained in misses:
            got_s = got + (f" -> {chained}" if chained else "")
            print(f"  {pid} [{kind}] {branch:<12} got {got_s}")
            print(f"      {text!r}")

    by_branch = Counter(p["expect_branch"] for p in batch.values())
    print(f"\nprompts by branch: {dict(by_branch)}")
    print("A miss on 'written' is a routing problem. A miss on 'spoken' only is STT.")


if __name__ == "__main__":
    main()
