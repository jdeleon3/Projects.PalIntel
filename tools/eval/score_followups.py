"""Score multi-turn follow-up resolution — ADR-0013, and Phase 2's exit criterion.

Routing evaluation becomes stateful the moment memory exists, which is why this is a
separate harness from score_router.py: the unit is a conversation, not an utterance, and
only the last turn is scored. The earlier turns are setup and their answers do not matter
except for what they leave in memory.

Two columns, and the second is the one that matters. `follow-up` is coverage - did the
referent resolve. `negative` is the price: cases where memory must NOT fire, either
because the question is fresh, or because it opens like a follow-up but carries its own
verb, or because there is nothing left to refer to. A run that scores 12/12 on the first
column and loses the second has made the system worse, since a stale referent produces a
card that looks entirely authoritative.

Usage:
    python tools/eval/score_followups.py                 # the deterministic stub
    python tools/eval/score_followups.py --router auto   # whatever build_router picks
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.config import RouterConfig  # noqa: E402
from palintel.knowledge import KnowledgeBase  # noqa: E402
from palintel.memory import Memory  # noqa: E402
from palintel.pipeline import Pipeline, build_router  # noqa: E402
from palintel.routing import StubRouter  # noqa: E402
from palintel.tools import Decline  # noqa: E402

CASES = Path(__file__).parent / "followups.json"


def check(outcome, case: dict) -> tuple[bool, str]:
    """Did the last turn produce what the case expects?"""
    call = outcome.call
    want = case.get("expect")

    if want is None:
        if not isinstance(call, Decline):
            return False, f"answered {call.name}({call.args})"
        if case.get("restate") and not call.needs_restatement:
            return False, f"declined without asking to restate: {call.reason}"
        return True, "declined"

    if isinstance(call, Decline):
        return False, f"declined: {call.reason}"
    if call.name != want["tool"]:
        return False, f"{call.name}, wanted {want['tool']}"
    for key, value in want["args"].items():
        if call.args.get(key) != value:
            return False, f"{key}={call.args.get(key)!r}, wanted {value!r}"
    return True, "ok"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", default="stub")
    ap.add_argument("--ttl", type=float, default=300.0)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    kb = KnowledgeBase.load("1.0.2")
    if args.router == "stub":
        router = StubRouter(kb.lexicon, {n.resource for n in kb.nodes})
    else:
        router = build_router(kb, args.router, RouterConfig())
    print(f"router: {router.name}\n")

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    results = []
    for case in cases:
        # A fresh Pipeline per case, so one conversation cannot leak into the next -
        # which is the bug this whole harness is watching for.
        pipe = Pipeline(kb, router, memory=Memory(ttl=args.ttl))
        outcome = None
        for turn in case["turns"]:
            outcome = pipe.handle(turn, who="eval")
        ok, detail = check(outcome, case)
        results.append((case, ok, detail))

    for group, label in ((False, "follow-up"), (True, "negative")):
        rows = [r for r in results if bool(r[0].get("negative")) is group]
        passed = sum(1 for _, ok, _ in rows if ok)
        print(f"{label:<10} {passed}/{len(rows)}")
        for case, ok, detail in rows:
            if not ok or args.verbose:
                mark = "  ok  " if ok else "  FAIL"
                print(f"{mark} {case['id']}  {case['why']}")
                print(f"         {' / '.join(case['turns'])}")
                if not ok:
                    print(f"         -> {detail}")
        print()

    total = sum(1 for _, ok, _ in results if ok)
    print(f"total {total}/{len(results)}")
    sys.exit(0 if total == len(results) else 1)


if __name__ == "__main__":
    main()
