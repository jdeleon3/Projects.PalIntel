"""Score the deterministic fast path against the A5 transcripts. No model, no cost.

Phase 1 measured this with one tool registered, so "claimed nothing outside Q1" was
scored when there was no other tool to claim *for*. Registering `find_pal_spawns` gives
a keyword matcher its first real chance to be confidently wrong: Q1 and Q2 share almost
every phrasing and differ only in the entity, which is precisely the judgement a router
with no sentence context is worst at.

What matters here is PRECISION, not coverage. Every query the fast path claims is a query
the model never sees, so a wrong claim is not a slower answer - it is a wrong card the
player acts on. Coverage that the fast path declines still gets answered, just slower.

Usage:
    python tools/eval/score_fast_path.py                    # shipped configuration
    python tools/eval/score_fast_path.py --sweep            # every cue width, both tools
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.knowledge import KnowledgeBase  # noqa: E402
from palintel.routing import DEFAULT_CUES, PAL_CONFIDENT, StubRouter  # noqa: E402
from palintel.tools import Decline, ToolCall  # noqa: E402

EVAL = REPO / "data" / "stt_eval"

# Which query class a prompt belongs to. Classified from the GENERATOR'S CLEAN TEXT, not
# from the transcript: a regex over what STT heard scores its own mangling, and it got
# this wrong the first time - "can I get coal at this level" was labelled out-of-class
# and its correct answer counted as theft, when Phase 1 had deliberately added that exact
# phrasing to the cue list after reading it in a real session.
#
# The list is an allowlist of the location templates in the A5 prompt set, and drawing it
# is a judgement rather than a lookup. Two calls worth stating: "how do I get <E>" is a
# location question for a resource and a breeding-or-catching question for a Pal, so it
# splits on entity kind; and "what's the best way to catch <E>" / "is it worth going
# after <E> tonight" are advice, not locations, however much a location would help.
_LOCATION_TEMPLATES = re.compile(
    r"where (can i find|do|does|is|are|'s)"
    r"|nearest|closest|near my|around here|somewhere close|right now"
    r"|find me a|\bspawn|how do i get\b",
    re.I)

# The resource side needs no allowlist. Of the 19 resource-entity prompts in the A5 set,
# 18 ask where to get the stuff, in eighteen different phrasings ("show me a safe sulfur
# spot", "where should I set up for sulfur", "I need coal for a new base"). Enumerating
# them would be fitting the labels to the router. Exactly one is not a location question,
# and it is the one Phase 1 already named: an inventory query that mentions a resource.
_NOT_A_LOCATION = re.compile(r"do i have enough", re.I)


def classify(clean: str, expected: list[str], resources: set[str]) -> str:
    if any(e in resources for e in expected):
        return "other" if _NOT_A_LOCATION.search(clean) else "q1_resource"
    return "q2_pal" if _LOCATION_TEMPLATES.search(clean) else "other"


def score(router: StubRouter, rows: list[dict], kb: KnowledgeBase,
          ranked: dict[str, list]) -> dict:
    resources = set(kb.lexicon.resources())
    tally: Counter[str] = Counter()
    wrong: list[str] = []
    stolen: list[str] = []

    for row in rows:
        text = row["boosted_text"]
        cls = classify(row["clean"], row["expected"], resources)
        call = router.route(text, ranked[text])

        if isinstance(call, Decline):
            tally[f"{cls}_deferred"] += 1
            continue

        # A claim. Is it the right tool, and the right entity?
        want_tool = {"q1_resource": "find_resource_nodes",
                     "q2_pal": "find_pal_spawns"}.get(cls)
        got = call.args.get("resource") or call.args.get("pal")

        if cls == "other":
            tally["other_claimed"] += 1
            stolen.append(f"{call.name}({got}) <- {text!r}")
        elif call.name != want_tool:
            tally[f"{cls}_wrongtool"] += 1
            wrong.append(f"{call.name}({got}) for {row['expected']} <- {text!r}")
        elif got in row["expected"]:
            tally[f"{cls}_right"] += 1
        else:
            tally[f"{cls}_wrong"] += 1
            wrong.append(f"{got} != {row['expected']} <- {text!r}")

    return {"tally": tally, "wrong": wrong, "stolen": stolen}


def report(name: str, res: dict, totals: Counter) -> None:
    t = res["tally"]
    row = [name]
    for cls in ("q1_resource", "q2_pal"):
        n = totals[cls]
        right, bad = t[f"{cls}_right"], t[f"{cls}_wrong"] + t[f"{cls}_wrongtool"]
        row.append(f"{right}/{n}")
        row.append(str(bad))
    row.append(f"{t['other_claimed']}/{totals['other']}")
    print(f"  {row[0]:<22}{row[1]:>10}{row[2]:>8}{row[3]:>10}{row[4]:>8}{row[5]:>12}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="quiet")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--pal-floor", action="store_true",
                    help="sweep the Pal acceptance floor at a fixed cue width")
    args = ap.parse_args()

    kb = KnowledgeBase.load("1.0.2")
    results = json.loads((EVAL / args.set / "results.json").read_text(encoding="utf-8"))
    records = results.values() if isinstance(results, dict) else results
    clean = {p["id"]: p["text"] for p in
             json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]}
    rows = [r | {"clean": clean[r["id"]]} for r in records if r.get("boosted_text")]
    resources = set(kb.lexicon.resources())

    totals = Counter(classify(r["clean"], r["expected"], resources) for r in rows)
    print(f"{len(rows)} transcripts: {totals['q1_resource']} Q1, {totals['q2_pal']} Q2, "
          f"{totals['other']} other classes\n")
    print(f"  {'router':<22}{'Q1 right':>10}{'wrong':>8}{'Q2 right':>10}{'wrong':>8}"
          f"{'stolen':>12}")

    locatable = {n.resource for n in kb.nodes}
    configs: list[tuple[str, bool, float]] = []
    if args.pal_floor:
        for floor in (0.78, 0.85, 0.90, 0.95, 1.0):
            configs.append(("proximity", True, floor))
    elif args.sweep:
        for cues in ("standard", "proximity", "wide"):
            for pals in (False, True):
                configs.append((cues, pals, PAL_CONFIDENT))
    else:
        configs.append((DEFAULT_CUES, True, PAL_CONFIDENT))

    # Ranking is the expensive step and does not depend on the router configuration, so
    # it is done once rather than once per sweep row.
    ranked = {r["boosted_text"]: kb.lexicon.rank(r["boosted_text"]) for r in rows}

    detail = None
    for cues, pals, floor in configs:
        r = StubRouter(kb.lexicon, locatable, cues=cues, pal_spawns=pals,
                       pal_floor=floor)
        res = score(r, rows, kb, ranked)
        report(r.name, res, totals)
        detail = (r.name, res)

    name, res = detail
    for label, items in (("wrong entity or tool", res["wrong"]),
                         ("claimed outside both classes", res["stolen"])):
        if items:
            print(f"\n{name} - {label} ({len(items)}):")
            for line in items[:15]:
                print(f"   {line}")


if __name__ == "__main__":
    main()
