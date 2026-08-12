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
# ---------------------------------------------------------------- the class axis
#
# **The decision rule for the 2026-08-12 run, written before it was paid for**, as
# CLAUDE.md requires. The run is `--sample 60 --model gemini-3.6-flash --unified`,
# ~$0.22, and it exists to answer one question: does the class axis measure anything, or
# is the harness broken?
#
#   * The entity numbers must land within a few points of the recorded 88.8% exact /
#     3.9% wrong. A large move means the SAMPLE or the schema changed something, not the
#     router, and nothing about the class axis should be believed until that is explained.
#   * A class figure of exactly 100% or exactly 0% is a broken harness, not a result.
#   * Anything else is a first reading and is reported as one. n=60 across ten classes is
#     six per class at best, so no per-class number below n=10 is a measurement - the
#     output marks those "thin" rather than letting them be quoted.
#
# What would justify a full run afterwards: a wrong-class rate above ~10%, or any class
# scoring under 50% on n>=10. Neither is a tuning trigger on its own; both are reasons to
# spend $1.40 to find out whether the sample was noise.

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).parent))

from palintel.knowledge import KnowledgeBase  # noqa: E402
from palintel.routing import CANDIDATE_LIMIT  # noqa: E402
from palintel.routing_anthropic import PRICES, ClaudeRouter  # noqa: E402
from _router_tools import eval_tool_schemas  # noqa: E402
from palintel.tools import Decline  # noqa: E402

EVAL = REPO / "data" / "stt_eval"


def build_router(model: str, kb: KnowledgeBase, think: bool = False,
                 thinking_level: str | None = None, unified: bool = False,
                 class_set: str = "production"):
    """Construct the backend `model` names, plus its tool-name list.

    One place, so score_router.py and repeat_router.py cannot drift into scoring
    different registries and reporting the numbers as comparable.
    """
    locatable = {n.resource for n in kb.nodes}
    pals = kb.lexicon.pals()
    # find_pal_spawns is production from Phase 2 and comes from the shared registry;
    # only the not-yet-built tools are still injected here.
    extra = eval_tool_schemas(pals)

    # A "local:" prefix selects the Ollama-backed router. Same registry, same prompts,
    # same scoring - the enum moves from the tool schema into a decoding grammar.
    if model.startswith("local:"):
        from palintel.routing_local import LocalRouter
        r = LocalRouter(kb.lexicon, locatable, model=model.split(":", 1)[1],
                        extra_tools=extra, think=think)
        return r, r._schema["properties"]["tool"]["enum"]
    # Both hosted backends take the evaluation timeout, not the runtime one. A router
    # that takes 60s is a measurement here - cutting it off at the runtime bound would
    # score the timeout rather than the model, and would silently redefine what every
    # earlier run in this file's history measured.
    if model.startswith("gemini"):
        from palintel.routing_gemini import TIMEOUT_S, GeminiRouter
        from palintel.routing_unified import CLASS_TO_TOOL
        from palintel.routing_unified import PRODUCTION_CLASSES

        # **Which classes the run offers, and the default changed 2026-08-12.**
        #
        # It used to be all of `CLASS_TO_TOOL`, so the consolidated run offered exactly
        # what the per-class registry did - the right call when the question was
        # "consolidated versus per-class". It is the wrong call for measuring the router
        # the bot actually runs, and the first class-axis run showed why: **5 of 13
        # over-answers were the model picking `compare_pals` and `get_breeding_combo`,
        # classes this harness registers and the dispatcher does not have.** Those are
        # not router failures, they are the harness measuring a system that does not
        # exist - the mistake `unified_schema`'s own docstring warns about.
        offered = (PRODUCTION_CLASSES if class_set == "production"
                   else tuple(CLASS_TO_TOOL))
        r = GeminiRouter(kb.lexicon, locatable, model=model, extra_tools=extra,
                         thinking_level=thinking_level, timeout_s=TIMEOUT_S,
                         unified=unified,
                         classes=offered if unified else None)
        return r, r.tool_names
    r = ClaudeRouter(kb.lexicon, locatable, model=model, extra_tools=extra,
                     timeout_s=None)
    return r, [t["name"] for t in r._tools]


# query class -> the tool that answers it, for the class axis. Read from the router's own
# translation table rather than restated, so a class that gains or renames a tool cannot
# leave this scorer quietly measuring the old one.
def _tool_for_class() -> dict[str, str]:
    from palintel.routing_unified import CLASS_TO_TOOL

    mapping = dict(CLASS_TO_TOOL)
    # The branch batch (`add_branch_batch.py`, ids B##) predates the consolidated tool
    # and labels its prompts with its own shorter words. Both vocabularies are alive in
    # `prompts.json`, so both are accepted here rather than one being rewritten - those
    # 31 prompts are already recorded, and renaming a field they carry would make
    # `score_branches.py` and this file disagree about the same file.
    mapping.update({
        "counter": "plan_counters",
        "drops": "find_pal_drops",
        # A question carrying BOTH a counter cue and a location cue. The fast path
        # chains, so the counter is the head of the call and that is what is scored -
        # matching score_branches.py, which checks the chained spawn call separately.
        "ambiguous": "plan_counters",
    })
    return mapping


def score_class(row: dict, kind: str, tool_for: dict[str, str]) -> str | None:
    """Whether the router picked the right CLASS. None when the prompt does not say.

    **A second axis, reported beside the entity number and never folded into it**, so
    every figure recorded in STATUS and the roadmap stays the number it was.

    It exists because the entity axis cannot see class selection at all: `expected` is a
    set of names, and six of the twelve production classes name nothing - so
    `base_rating`, `general_knowledge` and an honest decline are the same event to it.

    `unsupported` is its own verdict rather than a failure. The corpus was written for
    assumption A5, which measured entity recognition and did not care whether a question
    was answerable, so a third of it asks for breeding combos and stamina numbers. For
    those, **declining is correct** and answering is the failure - which is the opposite
    of every other row, and collapsing the two would have scored the router's best
    behaviour as its worst.
    """
    want = row.get("expect_branch")
    if not want:
        return None
    if want == "unsupported":
        return "ok" if kind == "decline" else "over"
    if kind == "decline":
        return "declined"
    return "ok" if kind == tool_for.get(want) else "wrong"


def score_one(router, row: dict, kb: KnowledgeBase, entities: set[str],
              tool_for: dict[str, str] | None = None) -> dict:
    """Route one transcript and score it. The scoring rules live here only."""
    heard = row["boosted_text"]
    expected = set(row["expected"])

    t = time.perf_counter()
    call = router.route(heard, kb.lexicon.rank(heard, limit=CANDIDATE_LIMIT))
    latency_ms = (time.perf_counter() - t) * 1000

    if isinstance(call, Decline):
        got, kind = set(), "decline"
    else:
        # Collect entities by value, not by parameter name: the tools spell the slot
        # pal / parent_a / pal_b depending on arity, and which tool was selected is not
        # what A5 measures.
        # By value, not by parameter name: the tools spell the slot differently, and
        # the consolidated tool hands entities back in a list rather than a scalar - so
        # flatten one level. A no-op for the per-class registry, whose args are scalars.
        flat = []
        for v in call.args.values():
            flat.extend(v) if isinstance(v, list) else flat.append(v)
        got = {v for v in flat if isinstance(v, str) and v in entities}
        kind = call.name

    # `wrong` is any entity the speaker did not say - except a variant of one they did.
    # The output is two titled Discord cards on a second screen, so naming Menasting
    # alongside Menasting Terra is an over-answer the player resolves at a glance, while
    # naming Pyrin for Pierdon is the confidently-wrong answer ADR-0007 refuses to ship.
    # Collapsing the two hid which failure a model actually had: Qwen3's over-naming was
    # mostly cross-family and stayed wrong under this rule, Gemini's was not.
    extra = got - expected
    over = {e for e in extra if any(kb.lexicon.same_family(e, x) for x in expected)}
    wrong = bool(extra - over)
    if not expected:
        # No-entity prompt: naming anything is a hallucination, declining is correct.
        hit = exact = not got
    else:
        hit = bool(got & expected)     # lenient: kept so earlier runs stay comparable
        exact = got == expected        # headline: every slot right, none invented

    u = router.last_usage
    return {"id": row["id"], "heard": heard, "group": row.get("group", "?"),
            "expected": sorted(expected), "got": sorted(got), "kind": kind,
            "expect_branch": row.get("expect_branch"),
            "class_verdict": score_class(row, kind, tool_for or {}),
            # A transport failure is not a routing decision. Carried so the summary can
            # refuse to score a run that hit them rather than reporting a rate limit as
            # a bad router - which it did once, quietly, at 76.7%.
            "transient": bool(getattr(call, "transient", False)),
            "hit": hit, "exact": exact, "wrong": wrong,
            "latency_ms": round(latency_ms),
            # Named a variant of the right Pal as well as the right Pal. Renders as a
            # second card rather than a wrong answer - but capped: beyond two cards the
            # answer should become a clarifying follow-up, so this is tracked, not waved
            # through.
            "over_answered": bool(over),
            "cards": len(got),
            "in_tok": u.input if u else 0,
            # Billable output. Gemini reports reasoning separately as thoughtsTokenCount
            # and bills it at the output rate, so recording candidatesTokenCount alone
            # under-reported this backend's output by several-fold.
            "out_tok": (u.output + getattr(u, "thoughts", 0)) if u else 0,
            "cached_tok": u.cache_read if u else 0,
            "usd": round(u.usd, 5) if u else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    ap.add_argument("--unified", action="store_true",
                    help="register one answer_query tool instead of one per class "
                         "(01-architecture.md section 7 note 4). Re-measures A5 across "
                         "the change rather than assuming it neutral.")
    # No default. Opus 5 used to be it, and at $1.11 per 80-prompt run an accidental
    # bare invocation is an expensive mistake - especially now that Opus is out of the
    # evaluation on cost grounds. Make the caller name the model they are paying for.
    ap.add_argument("--model", required=True,
                    help="claude-haiku-4-5 | gemini-3.6-flash | local:qwen3:8b | ...")
    ap.add_argument("--classes", default="production", choices=["production", "all"],
                    help="which query classes the unified tool offers. `production` "
                         "is what the bot dispatches and is the default since "
                         "2026-08-12; `all` reproduces the pre-2026-08-12 runs, "
                         "which offered classes the dispatcher does not have.")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N")
    ap.add_argument("--sample", type=int, default=0,
                    help="score a stratified slice of N, proportional across the "
                         "difficulty bands. Unlike --limit, which takes the first N and "
                         "is ordered by recording session rather than by anything "
                         "meaningful - an 8-prompt --limit check passed a schema bug "
                         "whose every failure was a tower question.")
    ap.add_argument("--think", action="store_true",
                    help="local models only: enable the model's own thinking mode. "
                         "Both hosted baselines thought, so this is the fair comparison; "
                         "without it the local run is the latency variant.")
    # Gemini only. Declines cost ~3.7x the thinking of a routed call and land ~2.6x the
    # latency, so the level is the lever on the decline tail. Omitted means the model's
    # default, which is what every earlier run measured.
    ap.add_argument("--thinking-level", choices=("minimal", "low", "high"),
                    help="gemini only: cap reasoning effort per request")
    args = ap.parse_args()

    results_path = EVAL / args.condition / "results.json"
    if not results_path.exists():
        sys.exit(f"No transcripts at {results_path}.\n"
                 f"  Run tools/eval/score_stt.py --condition {args.condition} first.")

    rows = json.loads(results_path.read_text(encoding="utf-8"))

    # `expect_branch` lives on the PROMPT and results.json predates it, so it is joined
    # by id rather than re-recorded. Recordings on disk stay valid - nothing about the
    # audio or the transcript changes - and a prompt set labelled after a recording
    # session still scores the session it describes.
    prompts_path = EVAL / "prompts.json"
    if prompts_path.exists():
        by_id = {p["id"]: p.get("expect_branch")
                 for p in json.loads(prompts_path.read_text(encoding="utf-8"))["prompts"]}
        for r in rows:
            if by_id.get(r["id"]):
                r["expect_branch"] = by_id[r["id"]]
        labelled = sum(1 for r in rows if r.get("expect_branch"))
        print(f"  class labels: {labelled} of {len(rows)} transcripts carry one "
              f"(run tools/eval/add_class_batch.py to widen)\n")

    # The no-entity prompts ("what should I research next") are kept, not filtered: they
    # are the false-positive test. Naming any entity there is a hallucinated entity.
    if args.limit:
        rows = rows[:args.limit]
    elif args.sample:
        # Proportional by band, deterministic, and at least one from each so a band
        # cannot vanish from a check that claims to cover them.
        import random
        from collections import defaultdict
        bands = defaultdict(list)
        for r in rows:
            bands[r.get("difficulty") or r.get("group") or "?"].append(r)
        picked, rng = [], random.Random(0)
        for band, group in sorted(bands.items()):
            take = max(1, round(args.sample * len(group) / len(rows)))
            picked += rng.sample(group, min(take, len(group)))
        rows = sorted(picked, key=lambda r: r["id"])
        print(f"  stratified sample: {len(rows)} of "
              f"{sum(len(g) for g in bands.values())} across {len(bands)} bands\n")

    kb = KnowledgeBase.load("1.0.2")
    entities = set(kb.lexicon.canonical_names)
    router, tool_names = build_router(args.model, kb, think=args.think,
                                      thinking_level=args.thinking_level,
                                      unified=args.unified,
                                      class_set=args.classes)

    # A prompt asking for an entity no registered tool can name is unanswerable by
    # construction, and the router is right to decline it. Scoring it as a miss measures
    # the prompt set, not the router. This caught 4 crude_oil prompts: crude_oil is in
    # the lexicon but has no extracted map nodes, so it never enters the resource tool's
    # enum - the declines were correct behaviour being counted as failures.
    expressible = set(kb.lexicon.pals()) | {n.resource for n in kb.nodes}
    unscoreable = [r for r in rows
                   if r["expected"] and not set(r["expected"]) <= expressible]
    if unscoreable:
        missing = sorted({e for r in unscoreable for e in r["expected"]}
                         - expressible)
        print(f"  excluding {len(unscoreable)} prompt(s): no tool can name "
              f"{missing} - declining them is correct, so scoring them is a "
              f"measurement of the prompt set\n")
        rows = [r for r in rows if r not in unscoreable]

    print(f"model={args.model}  condition={args.condition}  prompts={len(rows)}")
    print(f"tools={tool_names}\n")

    # Load weights before timing: a cold load is seconds and would land in the p95 as if
    # it were per-query latency.
    if hasattr(router, "warmup"):
        print(f"warmup: model loaded in {router.warmup():.1f}s\n")

    tool_for = _tool_for_class()
    scored = []
    t0 = time.perf_counter()
    for r in rows:
        s = score_one(router, r, kb, entities, tool_for)
        scored.append(s)

        mark = ("WRONG" if s["wrong"] else "OK  " if s["exact"]
                else "part " if s["hit"] else "miss ")
        print(f"  {mark} {s['id']}  expected={s['expected']}  "
              f"got={s['got'] or s['kind']}  ({s['latency_ms']}ms)")
        print(f"        heard: {s['heard'][:70]}")

    def block(rows: list[dict], label: str) -> None:
        if not rows:
            return
        n = len(rows)
        e = sum(x["exact"] for x in rows)
        h = sum(x["hit"] for x in rows)
        w = sum(x["wrong"] for x in rows)
        d = sum(1 for x in rows if x["kind"] == "decline")
        print(f"  {label:<24}{e}/{n} exact = {e / n * 100:5.1f}%   "
              f"(lenient {h / n * 100:5.1f}%)   wrong {w}   declined {d}")

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
    exacts = sum(s["exact"] for s in scored)
    hits = sum(s["hit"] for s in scored)
    wrongs = sum(s["wrong"] for s in scored)
    partial = sum(1 for s in scored if s["hit"] and not s["exact"])
    declines = sum(1 for s in scored if s["kind"] == "decline")
    lat = sorted(s["latency_ms"] for s in scored)

    print("\n" + "=" * 68)
    print(f"  correct entity   {exacts}/{total} = {exacts / total * 100:5.1f}%   "
          f"(every slot right, nothing invented)")
    print(f"  WRONG entity     {wrongs}/{total} = {wrongs / total * 100:5.1f}%   "
          f"(the failure that matters - a confident wrong answer)")
    print(f"  declined         {declines}/{total} = {declines / total * 100:5.1f}%   "
          f"(honest miss)")
    if partial:
        print(f"  partly right     {partial}/{total} = {partial / total * 100:5.1f}%   "
              f"(one slot of a multi-entity query right, another invented - counted "
              f"WRONG above, and scored as a hit before this run)")
    print(f"  lenient accuracy {hits}/{total} = {hits / total * 100:5.1f}%   "
          f"(any overlap with expected; the pre-v4 headline, kept for comparison)")

    # ----------------------------------------------------- the class axis
    #
    # A SECOND number, printed beneath the first and never folded into it, so everything
    # recorded in STATUS and the roadmap stays the figure it was. It measures what the
    # entity axis structurally cannot: six of the twelve classes name nothing, so on that
    # axis `base_rating`, `general_knowledge` and an honest decline are the same event.
    classed = [s for s in scored if s["class_verdict"]]
    if classed:
        verdicts = Counter(s["class_verdict"] for s in classed)
        ok = verdicts["ok"]
        print("\n" + "-" * 68)
        print(f"  correct CLASS    {ok}/{len(classed)} = {ok / len(classed) * 100:5.1f}%"
              f"   (the tool it chose, not the entity it named)")
        print(f"  wrong class      {verdicts['wrong']:4}   "
              f"(answered, with the wrong tool)")
        print(f"  declined         {verdicts['declined']:4}   "
              f"(a class we HAVE, refused - an honest miss, not a wrong card)")
        print(f"  over-answered    {verdicts['over']:4}   "
              f"(answered something marked `unsupported`, where declining is correct)")
        print(f"  unscored         {len(scored) - len(classed):4}   "
              f"(no expect_branch: ambiguous by construction)")

        per_class = {}
        for s in classed:
            b = per_class.setdefault(s["expect_branch"], [0, 0])
            b[1] += 1
            b[0] += s["class_verdict"] == "ok"
        print("\n  by class:")
        for cls, (right, n) in sorted(per_class.items(), key=lambda kv: -kv[1][1]):
            flag = "  <- thin" if n < 10 else ""
            print(f"    {cls:20}{right:4}/{n:<4} = {right / n * 100:5.1f}%{flag}")
    print(f"\n  latency  median {lat[len(lat) // 2]}ms   p95 {lat[int(len(lat) * 0.95) - 1]}ms"
          f"   total {time.perf_counter() - t0:.0f}s")
    # Cost covers every request the run billed, not the scored subset: the controls and
    # no-entity prompts are billed too. Reported per run because it was previously
    # estimated, and the estimate was out by 2.5x.
    spend = sum(s["usd"] for s in all_scored)

    # **The eval writes to the same ledger gameplay does.** Eval runs are the dominant
    # spend against the prepaid balance - $1.40 a full run against $0.005 a query - so a
    # balance that counted only gameplay would be wrong in the direction that matters,
    # and the failure it guards against is a depleted key arriving as a wall of declines.
    #
    # Its own session, prefixed so it never collides with a play session's timestamp and
    # so "what did evals cost this month" is a filter rather than a reconstruction.
    try:
        from palintel import spend as spend_mod
        ledger = spend_mod.SpendLog(f"eval-{time.strftime('%Y%m%d')}")
        for s in all_scored:
            ledger.record(spend_mod.Charge(
                at=time.time(), tool=s["kind"], path="eval",
                usd=s["usd"], model=args.model, billed=True,
                who=f"score_router --sample {args.sample or len(all_scored)}"))
        print(f"           logged to {ledger.path}")
    except Exception as e:      # never let bookkeeping break a paid run's output
        print(f"           (spend not logged: {e})")
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
    # Where the money actually went. Without this the natural assumption is that output
    # dominates; on Opus 5 it was 14% and cache reads of the tool schema were 74%. The
    # schema is 7 query-class tools each repeating the 313-name Pal enum - a deliberate
    # fidelity choice in the harness, not waste, but it should be visible per run.
    # Price table depends on the backend, not just the model string. Looking only in the
    # Anthropic table silently skipped this breakdown for every Gemini run.
    if args.model.startswith("gemini"):
        from palintel.routing_gemini import CACHED_INPUT_PRICE
        from palintel.routing_gemini import PRICES as GPRICES
        price, cache_rate = GPRICES.get(args.model), CACHED_INPUT_PRICE.get(args.model)
    else:
        price, cache_rate = PRICES.get(args.model), None
    if price and spend:
        p_in, p_out = price
        # Anthropic bills cache reads at 0.1x input; Gemini publishes a separate rate.
        # `in_tok` is the whole prompt on Gemini and the uncached remainder on Anthropic,
        # so the cached portion is subtracted out here to avoid double-counting it.
        p_cache = cache_rate if cache_rate is not None else p_in * 0.1
        cached = sum(s["cached_tok"] for s in all_scored)
        raw_in = sum(s["in_tok"] for s in all_scored)
        uncached = raw_in - cached if args.model.startswith("gemini") else raw_in
        parts = [("output (incl. thinking)", sum(s["out_tok"] for s in all_scored) * p_out),
                 ("schema cache reads", cached * p_cache),
                 ("uncached input", uncached * p_in)]
        for label, cents in sorted(parts, key=lambda x: -x[1]):
            usd = cents / 1e6
            print(f"           {label:<24} ${usd:6.3f}  {usd / spend * 100:>3.0f}%")
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
    stem = f"router_{args.model.replace(':', '-')}"
    if args.unified:
        stem += "_unified"
    # A thinking-level run is a different configuration of the same model, so it must not
    # land on the default run's filename - that is the baseline every sweep compares to.
    if args.thinking_level:
        stem += f"_think-{args.thinking_level}"
    if args.limit:
        # A truncated run must never overwrite a full one. This has already destroyed
        # the Opus 5 per-prompt detail once, leaving only the summary in the roadmap.
        stem += f"_first{args.limit}"
    # Release any provider-side cache the run created. Gemini bills cache storage per
    # token-hour, so leaving it to a 2h TTL charges for time nobody is using.
    if hasattr(router, "delete_cache"):
        router.delete_cache()

    transient = [s for s in scored if s.get("transient")]
    if transient:
        # Loud and unmissable. Five runs in an hour drained the Gemini key's prepaid
        # balance, and every 429 arrived as Decline(transient=True) which the summary
        # counted as an honest miss - a 13-point "regression" that was entirely billing.
        # Worth reading the 429 body rather than assuming a rate limit: it names the
        # cause, and a depleted balance does not come back on its own.
        print()
        print("=" * 68)
        print(f"  !! {len(transient)} of {len(scored)} prompts failed in TRANSPORT, not "
              f"routing (rate limit, timeout).")
        print(f"  !! Every one is scored as a decline, so the accuracy above is a FLOOR "
              f"on a broken run, not a measurement.")
        print(f"  !! Check the cause before re-running - a 429 is a rate limit OR a "
              f"depleted prepaid balance, and only one of those goes away on its own. "
              f"Affected: "
              f"{', '.join(s['id'] for s in transient[:8])}"
              f"{' ...' if len(transient) > 8 else ''}")
        print("=" * 68)

    out = EVAL / args.condition / f"{stem}.json"
    out.write_text(json.dumps(scored, indent=2), encoding="utf-8")
    print(f"detail -> {out}")


if __name__ == "__main__":
    main()
