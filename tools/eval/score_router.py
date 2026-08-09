"""Measure router entity resolution on real STT transcripts (assumption A5).

This is the measurement ADR-0016 deferred. Earlier evals scored the corrector in
isolation and found 61.5% - but the corrector was only ever meant to rank, with the
router making the confidence call using sentence context. This scores the layer that
actually decides.

Input is the *recorded* transcripts, not the prompt text: the point is what the router
does with "healthsphere" and "Lee's bunk", which is what the pipeline really receives.

Tools for every query class the prompt set exercises are registered, most of them not
yet built (see _router_tools.py). Without them a Pal question has nowhere to put its
entity and can only be scored "declined", which measures the tool registry rather than
entity resolution - with only Q1 and Q2 registered, 24 of 36 utterances were unroutable
by construction.

    python tools/eval/score_router.py --condition quiet [--model claude-opus-5]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N")
    ap.add_argument("--think", action="store_true",
                    help="local models only: enable the model's own thinking mode. "
                         "Both hosted baselines thought, so this is the fair comparison; "
                         "without it the local run is the latency variant.")
    args = ap.parse_args()

    results_path = EVAL / args.condition / "results.json"
    if not results_path.exists():
        sys.exit(f"No transcripts at {results_path}.\n"
                 f"  Run tools/eval/score_stt.py --condition {args.condition} first.")

    rows = json.loads(results_path.read_text(encoding="utf-8"))
    # The no-entity prompts ("what should I research next") are kept, not filtered: they
    # are the false-positive test. Naming any entity there is a hallucinated entity.
    if args.limit:
        rows = rows[:args.limit]

    kb = KnowledgeBase.load("1.0.2")
    locatable = {n.resource for n in kb.nodes}
    pals = kb.lexicon.pals()
    entities = set(kb.lexicon.canonical_names)
    extra = [pal_spawn_schema(pals), *eval_tool_schemas(pals)]

    # A "local:" prefix selects the Ollama-backed router. Same registry, same prompts,
    # same scoring - the enum moves from the tool schema into a decoding grammar.
    if args.model.startswith("local:"):
        from palintel.routing_local import LocalRouter
        router = LocalRouter(kb.lexicon, locatable,
                             model=args.model.split(":", 1)[1], extra_tools=extra,
                             think=args.think)
        tool_names = router._schema["properties"]["tool"]["enum"]
    elif args.model.startswith("gemini"):
        from palintel.routing_gemini import GeminiRouter
        router = GeminiRouter(kb.lexicon, locatable, model=args.model,
                              extra_tools=extra)
        tool_names = router.tool_names
    else:
        router = ClaudeRouter(kb.lexicon, locatable, model=args.model,
                              extra_tools=extra)
        tool_names = [t["name"] for t in router._tools]

    print(f"model={args.model}  condition={args.condition}  prompts={len(rows)}")
    print(f"tools={tool_names}\n")

    # Load weights before timing: a cold load is seconds and would land in the p95 as if
    # it were per-query latency.
    if hasattr(router, "warmup"):
        print(f"warmup: model loaded in {router.warmup():.1f}s\n")

    scored = []
    t0 = time.perf_counter()
    for r in rows:
        heard = r["boosted_text"]
        expected = set(r["expected"])
        candidates = kb.lexicon.rank(heard)

        t = time.perf_counter()
        call = router.route(heard, candidates)
        latency_ms = (time.perf_counter() - t) * 1000

        if isinstance(call, Decline):
            got, kind = set(), "decline"
        else:
            # Collect entities by value, not by parameter name: the tools spell the slot
            # pal / parent_a / pal_b depending on arity, and which tool was selected is
            # not what A5 measures.
            got = {v for v in call.args.values()
                   if isinstance(v, str) and v in entities}
            kind = call.name

        if not expected:
            # No-entity prompt: naming anything is a hallucination, declining is correct.
            hit, wrong = not got, bool(got)
        else:
            hit = bool(got & expected)
            wrong = bool(got) and not hit
        u = router.last_usage
        scored.append({"id": r["id"], "heard": heard, "group": r.get("group", "?"),
                       "expected": sorted(expected), "got": sorted(got), "kind": kind,
                       "hit": hit, "wrong": wrong, "latency_ms": round(latency_ms),
                       "in_tok": u.input if u else 0, "out_tok": u.output if u else 0,
                       "cached_tok": u.cache_read if u else 0,
                       "usd": round(u.usd, 5) if u else 0.0})

        mark = "OK  " if hit else ("WRONG" if wrong else "miss ")
        print(f"  {mark} {r['id']}  expected={sorted(expected)}  got={sorted(got) or kind}"
              f"  ({latency_ms:.0f}ms)")
        print(f"        heard: {heard[:70]}")

    def block(rows: list[dict], label: str) -> None:
        if not rows:
            return
        n = len(rows)
        h = sum(x["hit"] for x in rows)
        w = sum(x["wrong"] for x in rows)
        d = sum(1 for x in rows if x["kind"] == "decline")
        print(f"  {label:<24}{h}/{n} correct = {h / n * 100:5.1f}%   "
              f"wrong {w}   declined {d}")

    # A control prompt is the bare name with no question ("Anubis."). It carries no
    # intent, so there is no tool to route it to and declining is the correct answer.
    # Scoring it as a router failure measures the prompt set, not the router - the
    # controls exist to exercise the audio pipeline. Headline number is utterances.
    utterances = [s for s in scored if s["group"] == "utterance"]
    controls = [s for s in scored if s["group"] == "control"]

    print("\n" + "=" * 68)
    block(utterances, "utterances")
    block(controls, "bare-name controls")
    if controls:
        print("  (controls are a bare noun with no question - declining is correct)")

    all_scored = scored
    scored = utterances or scored
    total = len(scored)
    hits = sum(s["hit"] for s in scored)
    wrongs = sum(s["wrong"] for s in scored)
    declines = sum(1 for s in scored if s["kind"] == "decline")
    lat = sorted(s["latency_ms"] for s in scored)

    print("\n" + "=" * 68)
    print(f"  correct entity   {hits}/{total} = {hits / total * 100:5.1f}%")
    print(f"  WRONG entity     {wrongs}/{total} = {wrongs / total * 100:5.1f}%   "
          f"(the failure that matters - a confident wrong answer)")
    print(f"  declined         {declines}/{total} = {declines / total * 100:5.1f}%   "
          f"(honest miss)")
    print(f"\n  latency  median {lat[len(lat) // 2]}ms   p95 {lat[int(len(lat) * 0.95) - 1]}ms"
          f"   total {time.perf_counter() - t0:.0f}s")
    # Cost covers every request the run billed, not the scored subset: the controls and
    # no-entity prompts are billed too. Reported per run because it was previously
    # estimated, and the estimate was out by 2.5x.
    spend = sum(s["usd"] for s in all_scored)
    out_toks = sorted(s["out_tok"] for s in all_scored)
    schema_tok = max((s["cached_tok"] for s in all_scored), default=0)
    in_toks = sorted(s["in_tok"] for s in all_scored)
    print(f"  cost     ${spend:.2f} over {len(all_scored)} requests"
          f"  = ${spend / len(all_scored):.4f}/req")
    if schema_tok:
        print(f"           tool schemas {schema_tok} tok cached (billed once at 1.25x, "
              f"then 0.1x)")
    else:
        # No cache does not mean no schema. The local path genuinely keeps the enum out
        # of context (grammar); Gemini ships it as function declarations and simply is
        # not billed a separate cache line. Reporting those the same way would hide a
        # ~30x difference in prompt size.
        med = in_toks[len(in_toks) // 2]
        where = ("enum is in the grammar, not the context" if med < 5000
                 else "enum ships in the tool schemas, uncached")
        print(f"           no cached schema - prompt median {med} tok ({where})")
    print(f"           output median {out_toks[len(out_toks) // 2]} tok, "
          f"max {out_toks[-1]} tok")
    for k in ("routed", "decline"):
        g = [s for s in all_scored
             if (s["kind"] == "decline") == (k == "decline")]
        if g:
            m = sorted(x["out_tok"] for x in g)
            print(f"           {k:<8} n={len(g):<3} median {m[len(m) // 2]:>4} out tok")
    print("=" * 68)
    print(f"\nA5 target: >=95% entity accuracy.  Achieved: {hits / total * 100:.1f}%  "
          f"-> {'PASS' if hits / total >= 0.95 else 'FAIL'}")

    # ":" is legal in a model id and illegal in a Windows filename.
    out = EVAL / args.condition / f"router_{args.model.replace(':', '-')}.json"
    out.write_text(json.dumps(scored, indent=2), encoding="utf-8")
    print(f"detail -> {out}")


if __name__ == "__main__":
    main()
