"""Replay REAL logged utterances through the current router - the two-step version.

Step 1 (--stub, $0): every logged utterance through the fast path alone. No API calls;
this is `score_item_branch.py`'s exact technique pointed at real play instead of the
synthetic eval corpus, because real phrasing has found things a written prompt list
never did (see the roadmap's whole history of play sessions).

Step 2 (--model, real spend): only the utterances that STILL fall through to the model
under the new fast path are sent to the live router - `build_router`'s own production
stack, so the fast path claims what it would claim in production and only the remainder
is billed. Cost is bounded by that remainder, not by the corpus size.

Both compare against what the row ALREADY LOGGED, not against each other - a session log
is ground truth for "what happened," and the question is whether today's code would do
something different, not whether it matches some external answer key.

Usage:
    python tools/eval/replay_sessions.py --stub
    python tools/eval/replay_sessions.py --model            # spends money - see above
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.knowledge import KnowledgeBase   # noqa: E402
from palintel.tools import Decline             # noqa: E402


def load_rows() -> list[dict]:
    """Every logged utterance across every session, in file order.

    `set(r) == {"uid", "message_id"}` rows (the join lines `attach_message` writes) and
    bare feedback rows carry no `heard` text and are not utterances to replay.
    """
    rows = []
    for f in sorted(glob.glob(str(REPO / "data" / "sessions" / "*" / "log.jsonl"))):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "heard" in row:
                row["_session"] = Path(f).parent.name
                rows.append(row)
    return rows


def old_outcome(row: dict) -> str:
    return "decline" if row.get("outcome") == "declined" else (row.get("tool") or "?")


def run_stub(rows: list[dict]) -> list[dict]:
    """$0. Every row through the fast path alone - exactly what build_router wires as
    `.fast`, reconstructed here rather than imported so this never needs a live
    credential to run."""
    from palintel.pipeline import _corpus_probe, _counterable, _has_dataset
    from palintel.routing import StubRouter

    kb = KnowledgeBase.load("1.0.2")
    by_count: dict[str, int] = {}
    for n in kb.nodes:
        by_count[n.resource] = by_count.get(n.resource, 0) + 1
    locatable = sorted(by_count, key=lambda r: (-by_count[r], r))
    counterable = _counterable(kb.game_version)

    fast = StubRouter(kb.lexicon, locatable, cues="wide",
                      counters=bool(counterable), counterable=counterable,
                      progression=_has_dataset(kb.game_version, "tech.json"),
                      base_sites=kb.base_radius is not None,
                      corpus=_corpus_probe(kb.game_version),
                      item_lexicon=kb.item_lexicon)

    out = []
    for row in rows:
        text = row["heard"]
        call = fast.route(text, kb.lexicon.rank(text), [])
        new = "decline" if isinstance(call, Decline) else call.name
        out.append({**row, "_new": new,
                   "_new_args": {} if isinstance(call, Decline) else call.args})
    return out


def run_model(rows: list[dict]) -> list[dict]:
    """Real spend, bounded to whatever still reaches the model under the new fast path.

    Uses `build_router` itself - the exact production stack - so cost is exactly what
    production would have paid for these same utterances today, not an estimate. Cost
    per call goes through `spend.charge_from`, the same accounting the bot itself uses,
    rather than reading a usage object's fields by guess.

    **Logged to the same ledger gameplay and `score_router.py` write to**, under an
    `eval-<date>` session - see that script's own note on why: this is real spend
    against the prepaid balance, and a balance that only counted gameplay would be wrong
    in the direction that matters. Skipping this once already cost this file an honest
    accounting - the first run of this script spent $0.2783 with nothing recorded, the
    exact "two files disagree" shape this project has been bitten by before."""
    import time as _time

    from palintel import spend
    from palintel.pipeline import build_router

    kb = KnowledgeBase.load("1.0.2")
    router = build_router(kb, prefer="gemini")
    ledger = spend.SpendLog(f"eval-{_time.strftime('%Y%m%d')}")

    out = []
    billed = 0
    for row in rows:
        text = row["heard"]
        candidates = kb.lexicon.rank(text)
        call = router.route(text, candidates, [])
        new = "decline" if isinstance(call, Decline) else call.name
        usage = getattr(call, "usage", None)
        charge = spend.charge_from(usage, new, "replay")
        ledger.record(charge)
        if charge.billed:
            billed += 1
        out.append({**row, "_new": new,
                   "_new_args": {} if isinstance(call, Decline) else call.args,
                   "_billed": charge.billed, "_usd": charge.usd})
    print(f"  ({billed} of {len(rows)} actually reached the model)")
    print(f"  logged to {ledger.path}")
    return out


def report(replayed: list[dict], label: str, stub_only: bool) -> None:
    """`stub_only` changes what "regression" MEANS.

    When replaying the fast path alone (Step 1), most rows were originally answered by
    the MODEL - meaning the OLD fast path never claimed them either, and the stub
    declining now is simply the same non-coverage continuing, not a new failure. A real
    regression at this step is a row the fast path used to claim (`path == "fast"` in
    the log) that now declines or answers something different - that is the only
    comparison the stub-alone run can make honestly, because it has no model behind it
    to fall through to the way production does.
    """
    print(f"\n=== {label}: {len(replayed)} utterances ===\n")
    tally: Counter = Counter()
    changes = []
    for row in replayed:
        old = old_outcome(row)
        new = row["_new"]
        was_fast = row.get("path") == "fast"
        if old == new:
            tally["unchanged"] += 1
            continue
        if stub_only and not was_fast:
            # The old fast path never claimed this either (it went to the model) - a
            # stub-alone decline here is expected, not a finding. Counted so the total
            # still adds up, not printed as a change.
            tally["still deferred to the model, as before"] += 1
            continue
        if old == "decline" and new != "decline":
            tally["NEW COVERAGE (was decline, now answers)"] += 1
        elif old != "decline" and new == "decline":
            tally["REGRESSION (fast path used to claim this, now declines)"] += 1
        elif old == "find_item_source" or new == "find_item_source":
            tally["item_source involved"] += 1
        else:
            tally["tool changed"] += 1
        changes.append((row["_session"], row["uid"], old, new, row["heard"]))

    for k, n in sorted(tally.items()):
        print(f"  {k:<52} {n}")
    if changes:
        print(f"\n{len(changes)} changed (excluding expected model deferrals):")
        for session, uid, old, new, heard in changes:
            print(f"  [{session}:{uid}] {old} -> {new}")
            print(f"      {heard!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stub", action="store_true", help="$0 - fast path only")
    ap.add_argument("--model", action="store_true",
                    help="real spend - full production stack for whatever the fast "
                         "path does not claim")
    args = ap.parse_args()
    if not (args.stub or args.model):
        sys.exit("pass --stub, --model, or both")

    rows = load_rows()
    print(f"{len(rows)} logged utterances across "
         f"{len({r['_session'] for r in rows})} sessions")

    if args.stub:
        report(run_stub(rows), "Step 1: fast path replay ($0)", stub_only=True)

    if args.model:
        replayed = run_model(rows)
        report(replayed, "Step 2: full production stack (real spend)", stub_only=False)
        billed = [r for r in replayed if r.get("_billed")]
        total = sum(r.get("_usd") or 0.0 for r in billed)
        print(f"\n  billed queries: {len(billed)}   total: ${total:.4f}")


if __name__ == "__main__":
    main()
