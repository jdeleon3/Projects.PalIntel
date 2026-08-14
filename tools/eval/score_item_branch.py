"""Measure the item_source fast path BEFORE trusting it in production - the same order
every other branch here was built in (see score_fast_path.py, score_branches.py).

Two questions, and only one of them is about coverage:

1. Does it claim the item_source prompts that already exist (batch -1, B17-B31, written
   as `expect_path: "model"` because no fast path existed when they were written)?
2. Does it steal ANYTHING from the rest of the 271 A5 transcripts - the question that
   actually matters, because a branch that claims nothing is merely useless and a branch
   that claims the wrong thing is the failure this project refuses to ship.

Run against both the written prompt text and the quiet-condition STT transcript, same
split score_branches.py uses: a miss on 'written' is a routing problem, a miss on
'spoken' only is STT eating the item name before the router ever sees it.

Usage: python tools/eval/score_item_branch.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.knowledge import KnowledgeBase   # noqa: E402
from palintel.routing import StubRouter        # noqa: E402
from palintel.tools import Decline             # noqa: E402

EVAL = REPO / "data" / "stt_eval"


def main() -> None:
    prompts = json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    results = json.loads((EVAL / "quiet" / "results.json").read_text(encoding="utf-8"))
    heard = {r["id"]: r.get("boosted_text") or r.get("raw_text") for r in results}

    kb = KnowledgeBase.load("1.0.2")
    bosses = json.loads((REPO / "data" / "1.0.2" / "bosses.json").read_text(
        encoding="utf-8"))
    counterable = {b["name"].lower() for b in bosses["entries"] if b.get("name")}
    router = StubRouter(kb.lexicon, {n.resource for n in kb.nodes},
                        counters=True, counterable=counterable,
                        item_lexicon=kb.item_lexicon)

    def route(text: str):
        call = router.route(text, kb.lexicon.rank(text), [])
        return "decline" if isinstance(call, Decline) else call.name

    # --- 1. the 11 written-for-item_source prompts ---------------------------------
    item_prompts = [p for p in prompts if p.get("batch") == -1
                    and p.get("expect_branch") == "item_source"]
    print(f"batch -1 item_source prompts: {len(item_prompts)}\n")

    tally: Counter = Counter()
    for p in sorted(item_prompts, key=lambda p: p["id"]):
        for kind, text in (("written", p["text"]), ("spoken", heard.get(p["id"]))):
            if not text:
                tally["not transcribed"] += 1
                continue
            got = route(text)
            ok = got == "find_item_source"
            tally[f"{kind}: {'claimed' if ok else 'MISS - ' + got}"] += 1
            print(f"  {p['id']} [{kind:>7}] {'OK ' if ok else 'MISS'} {got:<20} {text!r}")

    print()
    for k, n in sorted(tally.items()):
        print(f"  {k:<34} {n}")

    # --- 2. does it steal from anything else? ---------------------------------------
    print(f"\nsweeping all {len(prompts)} prompts for item_source theft "
         "(claims on a prompt that is not batch -1 / item_source)...")
    stolen = []
    for p in prompts:
        if p.get("batch") == -1 and p.get("expect_branch") == "item_source":
            continue  # already scored above
        for kind, text in (("written", p.get("text")), ("spoken", heard.get(p.get("id")))):
            if not text:
                continue
            if route(text) == "find_item_source":
                stolen.append((p.get("id"), kind, text))

    if stolen:
        print(f"  STOLEN: {len(stolen)} prompts the item branch should not have claimed")
        for pid, kind, text in stolen:
            print(f"    {pid} [{kind}] {text!r}")
    else:
        print("  0 stolen - the branch claims nothing outside item_source prompts")


if __name__ == "__main__":
    main()
