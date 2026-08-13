# 04 — Implementation Roadmap

## Strategy

Phase 0 exists because seven assumptions ([00-overview.md](00-overview.md) §8) can each
invalidate significant design work, and all are cheap to test. **Do not write production
code until Phase 0 completes.**

After that, build one query class end to end before broadening. A working thin slice proves
the pipeline; further classes are incremental.

Phases 3 and 4 are each a **Tier 1 + Tier 2 pair**, deliberately: pairing a deterministic
class with an advisory one in the same phase keeps the candidate-set discipline
([ADR-0010](adr/0010-three-tier-answer-model.md)) fresh rather than letting advisory
paths accumulate unchecked.

---

## Phase 0 — De-risk (target: 1 week)

Throwaway spikes. No production code.

| # | Task | Validates | Kill criterion |
|---|---|---|---|
| ~~0.1~~ | ~~Post a rich embed; read it in the overlay **while playing**~~ | ~~A1~~ | **Retired.** Output is Discord cards on a second screen; there is no overlay. See "A1 retired" below. |
| 0.2 | Capture per-speaker PCM via Pycord; write a WAV | — | Unusable → evaluate alternative Discord libraries |
| 0.3 | Locate the save directory; parse owned Pals, bases, **and unlocked tech** | A2, A6 | Unparseable → Q3/Q5/Q6 degrade to stateless; revisit [ADR-0005](adr/0005-save-file-player-state.md) |
| 0.4 | Derive the combination table from ranks; check ≥ 100 known combos | A3 | < 100% agreement outside exceptions → scrape explicit combos |
| 0.5 | Extract node placements via FModel; fit the world → map transform; verify ~20 nodes in-game | A4 | Transform underivable → Q1 not viable as specified |
| 0.6 | Record 20 utterances with hard Pal names; measure STT raw, with keyterm boosting, with fuzzy correction | A5 | < 95% after both defenses → redesign entity handling first |
| 0.7 | Survey sources for **licence terms** and structural quality | A7 | No licensable source → Q7 corpus must be hand-written; scope Tier 3 down |

**Exit criteria:** A4 confirmed (v1 depends on it). A2, A3, A5, A6, A7 either confirmed
or their fallback chosen and recorded as an ADR amendment. **A1 is retired, not deferred.**

**Progress: A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ · A7 ◐ · A1 ⊘ retired · A5 ❌ (~90% vs 95%, ~3% wrong)**

Remaining before Phase 1 can start:

| Spike | Blocker |
|---|---|
| **0.6 — STT accuracy (A5)** | Model and latency **resolved**; architecture **corrected** ([ADR-0016](adr/0016-entity-resolution-in-router.md)). Router measured in Phase 1: **Gemini 3.6 Flash ~90% exact, ~3% wrong**, still under the 95% gate. |
| 0.4 — breeding combos (A3) | Confirmed via `CombiRank` + `DT_PalCombiUnique`. Gates Phase 3B, not Phase 1. (Written as "Phase 3"; that phase was split 2026-08-11 and breeding is the half A3 gates.) |

### A1 retired — there is no overlay

**Decision: the output surface is Discord cards on a second screen. The in-game overlay is
not a target and never will be.** A1 asked whether cards stay legible in the Steam overlay
while playing; that question no longer exists, so the spike is retired rather than left
open.

This removes the only remaining *presentation* constraint on card design, and several
inherited assumptions go with it:

- **Card density is no longer a legibility risk.** A second screen has room, the channel
  scrolls, and the history persists. Density is now an editorial choice about what is
  useful, not a constraint imposed by a cramped translucent panel over gameplay.
- **Multiple cards per answer become viable.** Discord allows 10 embeds per message, and
  Paldeck variant families are always exactly 2 (83 of 203 slots have a variant; none has
  more), so a family answer is at most two cards, each titled with its own Pal. This is
  what makes the variant-family design below workable — the objection to it was that two
  overlapping answers could not be told apart on a cramped surface, and that objection is
  gone.
- **The "reduce card density" fallback in [ADR-0006](adr/0006-templated-cards.md) is moot**,
  as is "validated in the overlay" as an acceptance test for template design.

Docs that still describe the overlay as a viewing surface
([00-overview.md](00-overview.md), [README.md](README.md),
[01-architecture.md](01-architecture.md) §, ADR-0006, ADR-0009) predate this decision and
should be read as historical until amended.

### Spike 0.6 outcome — STT model and latency (resolved)

**Decision: faster-whisper `medium.en`, float16, local GPU** —
see [ADR-0015](adr/0015-local-gpu-stt.md).

| | idle | game running |
|---|---|---|
| median | 141 ms | **171 ms** |
| p95 | 210 ms | **295 ms** |
| accuracy (v1 set) | 88% | 88% |
| VRAM | — | ~930 MiB |

No perceptible frame impact during play. CPU inference was measured at **RTF 1.35** and is
not viable at any usable model size; the GPU is a 23× speedup *and* more accurate, since it
runs float16 rather than the int8 CPU required.

A wrong conclusion was reached and reversed here: the first benchmark ran silently on CPU
because CUDA failed to initialise, and hosted STT was briefly recommended on that basis.
The cause was a missing runtime library, not a real constraint. Recorded in ADR-0015
because the failure mode is subtle and worth not repeating.

### Spike 0.6 outcome — entity accuracy (A5): architecture corrected, verdict deferred

Two measurement errors were made and corrected here. Both are recorded because each
produced a confident wrong conclusion.

**Error 1 — an inflated pass.** The v1 prompt set scored 84%, but tested exactly the names
that had hand-written seed aliases (Lifmunk, Jormuntide, Depresso…), and carried only
**5 scored entities** in the utterance group that matters. It measured tuning, not
generalisation.

v2 corrects both: **40 prompts, 39 scored entities**, utterance-weighted, names sampled
across the whole lexicon. Score dropped to **61.5%**. Of 16 misses, **14 were names with
no seeded alias.**

**Error 2 — a false failure.** 61.5% would have fired ADR-0007's redesign trigger. Ranking
the lexicon instead of thresholding it showed the trigger would have been wrong:

| | threshold-and-decline | ranked availability |
|---|---|---|
| correct entity accepted / rank 1 | 61.5% | **79.5%** |
| top-3 | — | **89.7%** |
| top-10 | — | 94.9% |

Every headline failure — *"health sphere"* → Helzephyr, *"the nurse? I grew down"* →
Aegidron, *"car links"* → Cryolinx — ranks the correct Pal **first**. They were rejected
by a 0.78 threshold, not missed by the matcher.

The signal was never lost; it was being discarded by the layer least able to judge it.
Resolved by [ADR-0016](adr/0016-entity-resolution-in-router.md): the corrector emits
ranked candidates, the router decides with context.

**Fixes landed during this spike**
- `hotwords` replaces `initial_prompt` — the latter was *hurting*, dropping controls from
  75% to 50%. It is a context hint, not keyterm boosting.
- Whitespace/punctuation normalisation before matching, since STT splits invented words
  into English ones (*"Lee's bunk"* → `Leezpunk`).
- A **precision metric**, which was missing entirely. It immediately caught 9 spurious
  matches traced to a single bad alias — `"or"` for *ore*, which scores 0.80 against
  "for". Alias safety rules plus length-aware thresholds cut spurious matches 9 → 1.

**A5 verdict: deferred to Phase 1.** Router accuracy is now the binding constraint and
cannot be measured without a live model. The ceiling is 89.7% top-3 availability — an
upper bound on a perfect chooser, not an achievement. Two entities are unrecoverable at
any layer: **Majex** (rank 69) and **Omascul** (rank 29).

Remaining unmeasured: whether the router correctly decides an entity is *present*, and
behaviour on arbitrary phrasing (candidate generation excluded template frame words).

### A5 verdict — measured in Phase 1

`tools/eval/score_router.py`, Claude Opus 5, 40 recorded transcripts, quiet condition.

| | |
|---|---|
| Correct entity (36 utterances) | **31/36 = 86.1%** |
| **Wrong entity** | **0/36 = 0.0%** |
| Declined | 5 utterances + 3 no-entity prompts |
| A5 target | ≥ 95% → **FAIL** |

**86.1% against a 95% gate, but the 0% matters more.** The failure this project refuses
to ship is a card that confidently answers the wrong question, and it did not occur once.
Every miss was an explicit decline, several with a usable clarifying question — *"I can't
tell whether you meant Mycora, or a Pal whose name sounded like Korra"*. All three
no-entity prompts (*"what should I research next"*) declined rather than inventing a Pal,
which is the false-positive test.

The Phase 0 ceiling estimate (89.7% top-3) proved roughly right and its two named
unrecoverable entities split: **Majex resolved** — "how do I breed magics?" routed
correctly, the router recovering what ranked 69th in isolation — while **Omascul did
not**, the one genuine corrector recall failure ("a Moscow", absent from the top 10).

The five misses are three different problems, and only one is the router's:

| | Cause | Answer's rank |
|---|---|---|
| P06 "where do Piranha spawn" | genuine ambiguity — *Piranha* is an English word | 8 (0.57) |
| P15 "how do I breed Snark" | genuine ambiguity, outranked by Sparkit | 3 (0.60) |
| P23 "how do I breed my Korra" | over-conservative decline | **1** (0.77) |
| P28 "the nearest a Moscow" | corrector recall failure | **absent** |
| P32 "breed kitsun with pyrdon" | **nondeterminism** | 1 (1.00) + 2 (0.77) |

**P32 routed correctly on retry**, returning `check_breeding_pair(Kitsun, Pierdon)`. One
run of n=36 against a nondeterministic model therefore carries real variance, and 86.1%
should be read as a point estimate, not a measurement. Closing the last nine points needs
repeat runs to size that variance before any tuning, or the tuning optimises noise.

Latency on utterances: **median 2.9s, p95 6.6s** — see
[01-architecture.md](01-architecture.md) §7 note 4.

#### Variance sized before tuning

`tools/eval/router_variance.py`, 12 boundary prompts, one repeat, $0.32. Prompts whose
expected entity is an unambiguous rank-1 match were not re-run: they have no judgement
left in them, so they cannot move the headline and only the boundary was sampled.

**2 of 12 flipped, in opposite directions.** P32 routed correctly (`Kitsun`, `Pierdon`)
and **P19 "should I use Astridolium against the first tower" declined, having routed last
run** — so the second sample also scores **31/36 = 86.1%**, from a different set of
misses. The aggregate is reproducible; the per-prompt outcomes are not.

A 16.7% flip rate over 12 live prompts (Wilson 95%: 4.7–45%) puts the run-to-run standard
deviation at **≈1.3 utterances, or ±3.6 points**. Two consequences:

- The 8.9-point gap to the 95% gate is **larger than noise** — real, not a bad sample.
- A single n=36 run **cannot resolve a change smaller than ~4 points**. Any tuning must be
  scored with repeats or a larger prompt set, or the result is unreadable.

The instability is not spread evenly: every unstable prompt is a **rank-1 candidate
scoring 0.6–0.8** (P19 at 0.63, P32's second entity at 0.77), and P23 — the one
over-conservative decline — sits at 0.77 in that same band. The router's confidence call
in the 0.6–0.8 window is both the noise source and the only tuning target the misses
actually offer.

**Both flips were decline↔route, never a wrong entity.** The 0% wrong-entity result
survived a second sample, including the three prompts where the model changed its mind.

That bounds what tuning can buy. Of the four stable misses, P28 is a corrector recall
failure and P06/P15 are genuine ambiguities; winning P23 outright still lands at
**32/36 = 88.9%**. **Reaching 95% requires corrector recall work (Omascul), not router
tuning alone** — consistent with the 89.7% top-3 ceiling from Phase 0.

#### Haiku 4.5 comparison — the gap is candidate depth, not accuracy

Same 40 transcripts, `--model claude-haiku-4-5`. Haiku 4.5 predates adaptive thinking and
`effort` (both 400), so it runs a fixed 2,048-token thinking budget — `LEGACY_THINKING` in
`routing_anthropic.py`.

| | Opus 5 | Haiku 4.5 |
|---|---|---|
| Correct entity | 31/36 = 86.1% | **30/36 = 83.3%** |
| **Wrong entity** | 0 | **0** |
| No-entity prompts declined | 3/3 | **3/3** |
| Cost per 40-prompt run | $0.70 | **$0.19** |
| Latency median / p95 | 2.9s / 6.6s | 3.6s / 7.6s |

**One utterance apart — inside the ±3.6-point noise band, so the headline numbers are not
distinguishable.** The composition is, and it falls out along one axis: **how deep in the
candidate list each model can still find the answer.**

| Prompt | Answer's rank | Opus 5 | Haiku 4.5 |
|---|---|---|---|
| P32 breeding pair | 1 + 2 | miss (flake) | **hit** |
| P23 "my Korra" | 1 | miss (over-conservative) | **hit** |
| P15 "Snark" | 3 | miss | miss |
| P11 "healthsphere" | 4 | **hit** | miss |
| P06 "Piranha" | 8 | miss | miss |
| P25 "Nakhlim" | 13 | **hit** | miss |
| P28 "a Moscow" | 47 | miss | miss |
| P07 "magics" | 115 | **hit** | miss |

**Haiku resolves nothing below rank 2; Opus resolves past rank 100.** Every one of Haiku's
three extra misses is a prompt whose entity the corrector ranked 4th, 13th, or 115th — and
every one of them Opus recovered from the tool enum on sentence context alone. In the other
direction Haiku is *less* conservative: it committed on both rank-1 candidates Opus
declined or flaked on.

That converges with the recall ceiling above and makes the sequencing concrete: **corrector
recall work is not just the path to 95%, it is also what would make a 3.7× cheaper model
viable.** Get the right entity into the top 2–3 and Haiku's deficit disappears; leave it at
rank 13+ and only a frontier model recovers it.

Two caveats on the secondary numbers. The latency comparison is **not** a clean model
result — the 2,048-token budget is an untuned choice on my side, and Haiku's 285-token
median output against Opus's 140 is most of the gap; a smaller budget would likely close
it. And cost came in 3.7× apart rather than the 5× the price ratio implies, for the same
reason: Haiku spent about twice the output tokens.

#### Local model (Qwen3 8B) — matches the headline, fails the bar

`--model local:qwen3:8b`, Ollama 0.32.6, RTX 5080, grammar-constrained output. The Pal
enum is compiled into a GBNF grammar rather than shipped as a tool schema, so the prompt
is **557 tokens against Opus's 21,741** — the economic case for local is real.

| | Opus 5 | Haiku 4.5 | Qwen3 8B *think* | Qwen3 8B *no think* |
|---|---|---|---|---|
| Correct entity | 86.1% | 83.3% | **86.1%** | 77.8% |
| **Wrong entity** | **0** | **0** | **4 (11.1%)** | **5 (13.9%)** |
| Invented an entity on a no-entity prompt | 0/3 | 0/3 | **1/3** | 0/3 |
| Latency median / p95 | 2.9s / 6.6s | 3.6s / 7.6s | 3.8s / 7.4s | **0.9s / 1.5s** |
| Cost per 40-prompt run | $0.70 | $0.19 | **$0** | **$0** |

**Qwen3 8B with thinking reproduces Opus 5's headline number exactly — 31/36 — and is not
close to shippable.** Read the accuracy row alone and the conclusion is that an 8B local
model equals a frontier one and saves $0.70 a run. The wrong-entity row is the whole
argument for why that row was never the gate: same accuracy, four confidently wrong
answers, and a Pal invented for *"what does the Artisan tree do?"* — the false-positive
test both hosted models passed cleanly.

**The failure has a single mechanism, and it indicts the architecture rather than the
model.** [ADR-0016](adr/0016-entity-resolution-in-router.md) makes the corrector rank
without deciding — *"This class ranks. It does not decide."* — and delegates judgment to
the router. That delegation assumes the router **has** judgment. Without thinking, Qwen3
takes the ranked list as an answer key:

| | It picked | Which were | Matched on |
|---|---|---|---|
| P11 "against the first tower" | Maraith + Gorirat Terra | candidates #1 and #2 | `"against the"`, `"first tower"` |
| P37 "show me courts near my base" | Nitemary + Shroomer | candidates #1 and #2 | `"near my"`, `"show me"` |

Both are matches on **frame words** — the phrasing of the question, not the entity in it.
The hosted models discard those on sight. An 8B model cannot tell a spurious candidate
from a real one, so every unfiltered match the corrector emits becomes a live wrong
answer. Thinking mode recovers most of the accuracy (77.8% → 86.1%) but only reduces
wrong entities 5 → 4: **reasoning fixes the ranking, not the judgment.**

A second, related tell: the local model **over-names on 6 of 36 utterances**, returning
two Pals where the query means one (*"where can I find Kitsun?"* → `Kitsun` **and**
`Kitsun Noct`). The scorer counts that as a hit because it intersects the expected set,
which flatters the local number — but a card cannot ask which one you meant, so it is a
wrong answer wearing a hit's clothing. Both hosted models over-named zero times. An
explicit prompt instruction against it did not stop it.

**Verdict: not viable as the router, on the safety bar rather than the accuracy one.** The
latency and cost results are genuinely attractive — 0.9s median, free, 3× faster than Opus
— which is exactly what makes the accuracy-only reading dangerous. Two things would have
to change before local is worth revisiting: the corrector would need to stop emitting
frame-word matches (a length- and stopword-aware filter, cheap and local), and the
candidate list would need to reach the router already trustworthy rather than merely
ranked. That is the same corrector-recall work the 95% gate needs — it is now blocking
two things instead of one.

#### Gemini 3.6 Flash — best measured accuracy, but one run

`--model gemini-3.6-flash`, function declarations carrying the same registry and the same
enums Claude receives, so this arm is like-for-like with the hosted baselines.

| | Opus 5 | Haiku 4.5 | **Gemini 3.6 Flash** | Qwen3 8B *think* |
|---|---|---|---|---|
| Correct entity | 86.1% | 83.3% | **91.7%** | 86.1% |
| **Wrong entity** | **0** | **0** | **1 (2.8%)** | 4 (11.1%) |
| Invented an entity on a no-entity prompt | 0/3 | 0/3 | **0/3** | 1/3 |
| Over-named (two Pals where one was meant) | 0 | 0 | **0** | 6 |
| Latency median / p95 | 2.9s / 6.6s | 3.6s / 7.6s | **2.0s** / 6.3s | 3.8s / 7.4s |
| Prompt tokens per query | 21,741 *cached* | 18,060 *cached* | **16,489 uncached** | 557 |

**33/36 is the highest number this prompt set has produced — and it is one run.** The
variance work above puts a single n=36 run at ±3.6 points, so a 5.6-point lead over Opus
is two utterances and roughly 1.5σ. **Gemini being better than Opus here is suggestive,
not established**, and it would need repeats to claim otherwise. What *is* clean is the
company it keeps: zero over-naming, zero invented entities on the no-entity prompts, and
one wrong entity rather than four.

Its three misses are informative. **P28 (Omascul, rank 47)** is the universal miss — every
model tested fails it, which is now strong evidence that it is a corrector-recall problem
and not a router problem. **P06** it got wrong rather than declining (`Pierdon` → `Pyrin`),
where both Claude models declined; it is marginally less conservative at the boundary.
**P22** it declined outright — *"where do Grisbolts spawn"*, with Grizzbolt ranked 1 at
0.94 — which every other model got, and which looks like a flake rather than a pattern.

Two caveats on the economics. Gemini ships the enum as tool schemas like Claude does, but
**uncached**: 16,489 tokens on every query, 593,609 input tokens across the run, against
Claude's write-once-then-read-at-0.1× profile. And the run is deliberately reported
**unpriced** — `routing_gemini.PRICES` is empty rather than populated from memory, because
this project has already published a cost estimate that was wrong by 2.5×
([67720b3](#)) and an invented per-token price would repeat that with less excuse.

One Gemini-specific constraint worth recording, since it shapes any future work here:
**`responseSchema` rejects an enum past roughly 2KB of values** — the 318-name lexicon
fails with a bare `INVALID_ARGUMENT`, and it is a size rather than a count limit (217 real
names pass, 218 fail; 300 short synthetic names pass). Function declarations carry the
identical enum without complaint. Structured-output mode is therefore not usable for
entity resolution at this vocabulary size; function calling is.

#### Batch 1 recorded — n doubled, and the scoring was wrong

80 clips (batches 0–1), 76 utterances. Two things changed at once, and they pull in
opposite directions, so both are reported.

**The scorer was over-crediting.** A hit was any overlap with the expected set, so
*"can I breed Kitsun with Pyrdun"* answered as `(Kitsun, Pyrin)` scored as a clean hit —
one of the two intersected. That is a breeding card naming the wrong parent, which is the
confidently-wrong answer [ADR-0007](adr/0007-answer-or-abstain.md) refuses to ship. The
headline is now **exact match** (every slot right, nothing invented) and `wrong` is **any
entity the speaker did not say**. The lenient figure is still printed so earlier runs stay
comparable.

| | exact | lenient | **wrong** | declined |
|---|---|---|---|---|
| **Gemini 3.6 Flash** | **85.5%** | 89.5% | 5.3% | 21.1% |
| Opus 5 | 77.6% | 78.9% | **2.6%** | 31.6% |
| Haiku 4.5 | 73.7% | 75.0% | 3.9% | 34.2% |
| Qwen3 8B *think* | 71.1% | 82.9% | **22.4%** | 18.4% |

**Gemini strictly dominates Opus 5**: 6 prompts it gets right that Opus does not, and
**zero** the other way. Exact McNemar p = 0.031. The 5.6-point lead at n=36 was called
suggestive-not-established; at n=76 it is established, and it widened to 7.9 points.
Haiku (p = 0.45) and Qwen3 (p = 0.33) are not separable from Opus.

**Nothing regressed — the new prompts are simply harder.** Restricted to the original 36,
every model reproduced its earlier number: Opus 86.1%, Gemini 91.7%, Qwen3 83.3%. The
whole drop comes from batch 1, which was built to be harder on purpose.

**Opus's conservatism stops being free.** It declines 31.6% against Gemini's 21.1%, and
seven of the eight prompts Gemini wins are Opus declining something plainly resolvable —
*"what does Vanwyrms drop"* (a plural), *"what level should Shroomr be"*, *"breeding combo
for Gizmos"*. At n=36 the trade read as pure upside: same accuracy, zero wrong. On harder
input it converts directly into misses, and Gemini buys 7.9 points for one extra wrong
answer in 76.

**Qwen3's real wrong-entity rate is 22.4%, not the 10.5% previously recorded.** Nine of its
"hits" contained an invented second entity. The local option is further from viable than
the first measurement suggested, not closer.

| difficulty | n | Opus 5 | Haiku | Gemini | Qwen3 |
|---|---|---|---|---|---|
| easy | 12 | 83% | 83% | 92% | 75% |
| medium | 15 | 93% | 73% | 93% | 80% |
| hard | 18 | 78% | 78% | **94%** | 61% |
| variant | 5 | 60% | 80% | 80% | 80% |
| frame_word | 4 | 50% | 50% | 75% | 50% |
| resource | 7 | 86% | 71% | 86% | 86% |
| **two_entity** | 6 | **17%** | **17%** | **17%** | **17%** |
| no_entity | 9 | 100% | 100% | 100% | 100% |

**Two-entity queries are broken for every model, at 1 of 6.** This is the sharpest result
in the run and it is not a router-quality problem: STT mangles *both* names and the
corrector has to recover both, so the failure compounds. *"is Omniscole faster than
Mimog"* (Omascul), *"is magics better than gemmas"* (Majex/Gumoss) — every model declines
or half-answers. **Q4 depends on this class**, so it needs its own decision rather than
riding on the single-entity number.

The false-positive test is now 9 prompts and **every model scored 9/9**, including Qwen3 —
its lone hallucination at n=3 was noise, as the variance work predicted.

#### Tuning round 1 — helps the capable models, hurts the weak one

Three changes, measured against the same 76 utterances. **Opus 5 is dropped from the
evaluation on cost**; the field is Haiku 4.5, Gemini 3.6 Flash, and Qwen3 8B.

1. **One shared routing policy.** `routing_anthropic.SYSTEM` and `routing_local.SYSTEM`
   had drifted apart, so Haiku and Gemini were being compared on *different
   instructions* — a confound in every earlier number. The judgment rules now live once
   in `routing.ROUTING_POLICY`; only the output-format sentence differs per backend.
2. **Decline policy rebalanced.** The old text said *"prefer decline over a coin flip"*.
   Measured, the router declined 21–34% and was wrong 3–5%, so the instruction was
   costing far more than it saved. It now says a plural or a misheard vowel is not
   ambiguity, and that declining an answerable query is also a failure.
3. **Candidate depth 10 → 15** (`routing.CANDIDATE_LIMIT`), the knee of the recall curve:
   recall@10 = 94.0%, @15 = 95.5%, flat to @100.

| | exact before | after | Δ | wrong | declined |
|---|---|---|---|---|---|
| **Gemini 3.6 Flash** | 85.5% | **86.8%** | +1.3 | 4 → **3** (3.9%) | 21.1% → 21.1% |
| Haiku 4.5 | 73.7% | **77.6%** | +3.9 | 3 → **5** (6.6%) | 34.2% → 27.6% |
| Qwen3 8B | 71.1% | **65.8%** | **−5.3** | 17 → **23** (30.3%) | 18.4% → 14.5% |

**No result here is statistically significant** — paired p = 0.375, 1.000, 0.289. At n=76
a 4-point move is about 3 utterances. These are directional readings, not wins.

**The mechanism is clean, and it differs by model capability.** Every one of Haiku's four
gains was a decline becoming a correct resolution — *Puffolt*, *Cawgnito*, *Xenolord*,
*Gumoss+Majex* — which is precisely what change 2 was for. Five of Qwen3's six losses were
the *same* failure in the other direction: a spurious second entity pulled from the newly
deeper candidate list (`Cryolinx`+Frostallion, `Demon Eye`+Maraith, `Paladius`+coal,
`Faleris`+Paladius, `Snock`+Snock Lux). **A deeper list helps a model that can reject
distractors and actively harms one that cannot**, and telling a weak model to be less
conservative just converts honest declines into confident errors.

Qwen3 also broke the false-positive test for the first time, inventing `Fuack` for a
no-entity prompt — 9/9 became 8/9.

**Kept, with the reservation stated.** Gemini leads and improved on both axes at once
(accuracy up, wrong down: its one recovered error was a half-wrong pair becoming an
honest decline). The pre-registered revert condition was a wrong-entity rate above ~5%:
**Gemini is at 3.9% and passes; Haiku is at 6.6% and does not.** If Haiku were the
candidate this would be reverted. Qwen3's regression is severe enough that local routing
is now further from viable than any earlier measurement suggested.

Attribution is inferred rather than isolated — three changes landed together — but the
failure shapes make it fairly legible: Haiku's gains are all decline→resolve (change 2),
Qwen3's losses are all added-distractor (change 3).

#### 200 recordings — Gemini 3.6 Flash wins decisively; A5 still fails

196 utterances, 192 scoreable. Qwen3 8B and Opus 5 are both out (accuracy and cost
respectively), so this is the two-horse result.

| | exact | wrong | declined | latency median |
|---|---|---|---|---|
| **Gemini 3.6 Flash** | **89.6%** | **3.1%** | 20.9% | **2.3s** |
| Haiku 4.5 | 72.9% | 5.2% | 36.7% | 4.6s |

**Paired McNemar: Gemini wins 35, Haiku wins 3, p = 6.7 × 10⁻⁸.** On the held-out batches
alone it is 28–2, same p. This is not a close call and does not need more data.

**Tuning round 1 generalised.** Gemini's held-out batches (2–4, never seen when the
decline wording was rewritten) score **88.3% with 1.7% wrong**, *better* than the 86.8%
and 5.3% on the batches it was tuned against. Haiku went the other way — 78.9% tuned-on
against 66.7% held out, declining 42.5% of held-out queries — which is what overfitting
looks like when it does happen, and a reason to keep reporting the split.

**Two corrections to earlier entries in this document.**

*"Two-entity queries are broken for every model" was a 6-prompt artifact.* At n=18 Gemini
scores 72%, and split by batch: **2/6 on batches 0–1, 11/12 on batches 2–4.** The original
six were disproportionately loaded with the hardest entities in the corpus — Majex (rank
193), Omascul (47), Pierdon (51). The recommendation that Q4 needed its own separate
decision is withdrawn.

*Four prompts were unanswerable by construction.* `crude_oil` is in the lexicon but has no
extracted map nodes, so it never enters the locate tool's enum — the router **cannot** name
it, and declining is correct. Those declines were being scored as misses. The v3 generator
introduced this by adding "crude oil" to the resource templates without checking it was
locatable; 17 such prompts exist across the 1000, 4 of them already recorded. The
generator now derives its resource list from the knowledge base, and `score_router.py`
excludes any prompt whose expected entities no registered tool can name — a general guard,
so a future lexicon/tool mismatch is caught rather than silently costing points.

**Where the remaining 10.4 points are**, now that the model question is settled:

| band | n | Gemini | note |
|---|---|---|---|
| hard | 45 | **98%** | acoustically hard names are effectively solved |
| medium | 33 | 91% | |
| easy | 21 | 90% | |
| frame_word | 16 | 88% | the corrector's spurious matches are being rejected |
| no_entity | 27 | **100%** | safety bar holds at n=27 |
| two_entity | 18 | 72% | |
| **variant** | 20 | **70%** | base-vs-variant disambiguation |
| resource | 16 | 69% → **100%** once crude_oil is excluded | |

**Cost: Gemini is the expensive option, not the cheap one.** At published rates
([pricing page](https://ai.google.dev/gemini-api/docs/pricing), $1.50/$7.50 per MTok with
thinking billed as output), the 200-prompt run costs **~$5.75 against Haiku's measured
$0.95 — about 6x more.** Two errors had hidden this and both are now fixed: the backend
recorded `candidatesTokenCount` as output and dropped `thoughtsTokenCount`, under-reporting
output ~25x (median 23 tok, actually 571); and the prices had been taken from asking Gemini
itself, which understated input 20x and output 25x. **Do not price a model by asking it.**

**~93% of that spend is one avoidable line item.** The ~16.7k-token tool schema is resent
uncached on all 196 requests — 3.27M input tokens. At the context-caching rate ($0.15/MTok)
that input drops from $4.91 to $0.49. Gemini context caching is not currently used at all;
`cachedContentTokenCount` is now recorded so it can be confirmed rather than assumed. Note
also that the eval deliberately registers all seven query classes; production Q1 ships 852
tokens of schema, not 16.7k, so eval cost is not production cost.

**Variant handling is now the single largest recoverable gap.** Failures are `Celaray` for
*Celaray Lux*, `Solmora`+`Solmora Lux` returned together, and clean transcripts like
*"Smokey Cryst"* and *"Loop Moon Cryst"* declined outright. The corrector ranks base and
variant as independent entities that both match the same audio, and nothing downstream
knows they are the same family.

#### Variant-suffix rule: tried, measured, reverted

A rule was added to `ROUTING_POLICY` stating that a spoken variant suffix (Cryst, Noct,
Lux, Terra…) disambiguates and that a base/variant pair is not ambiguity. The reasoning
looked sound: all 83 multi-word Pals share a base name with a real Pal, so every variant
query carries a guaranteed distractor, and the router was declining on rank-1 answers.

**It made the band it targeted worse.** Paired on the same 192 prompts, rule off → on:

| | off | on |
|---|---|---|
| exact | 91.1% | 88.5% |
| wrong | 2.6% | 3.1% |
| **variant band** | **18/20** | **15/20** |

Gained 3, lost 8, p = 0.227. Three regressions were variant prompts correct before and
declined after — `Dinossom Lux`, `Incineram Noct`, `Vanwyrm Cryst` — and it introduced a
hallucination on a no-entity prompt. Reverted.

**The diagnosis behind it was wrong in an instructive way.** The failures were read as the
router mistaking a base/variant pair for ambiguity. But the band was already at 90% (18/20)
before the rule; the six failures examined were selected *because* they had failed, and
generalising a mechanism from a filtered sample produced a rule that broke the 90% that was
already working. Spelling out a distinction the model was mostly handling implicitly made
it hesitate.

Held-out batch 5 scored 4/5 on variant prompts, which read as confirmation — at n=5 it
carried almost no information against a 192-prompt paired comparison pointing the other
way. **Note also that the run-to-run noise floor here is ~3 prompts** (89.6% and 91.1% on
identical config), so an 8-lost/3-gained swing is not cleanly separable from noise either;
the change is dropped for lack of evidence *for* it, not proof against it.

**Batch 5 raised the wrong-entity rate.** Held-out: 80.0% exact with **12.5% wrong** (5 of
40), against 88.5% and 3.1% on batches 0–4. Only one of the five is variant-related; the
rest are the router committing to a wrong candidate on heavily mangled input —
*"Cinnamom"* → `Cinnamoth` for Incineram, *"Astrum"* → `Astegon` for Astralym. That is
tuning round 1's decline-rebalancing, not the variant rule, and **it needs its own
measurement now that a fresh recording session has surfaced it.**

#### A5 final measurement — accepted at measured behaviour

**Router: Gemini 3.6 Flash.** 240 recordings, 236 utterances, 232 scoreable. Candidate
depth 15, shared routing policy, variant-suffix rule reverted, family-aware scoring.

| | exact | wrong | declined | no-entity |
|---|---|---|---|---|
| **All 232** | **88.8%** | **3.4%** | 20.7% | 32/33 |
| Seen (batches 0–4) | 90.6% | 2.1% | 20.3% | 26/27 |
| **Held out (batch 5)** | **80.0%** | **10.0%** | 22.5% | 6/6 |

| band | n | exact | | band | n | exact |
|---|---|---|---|---|---|---|
| easy | 24 | 92% | | resource | 15 | 93% |
| hard | 54 | 91% | | frame_word | 20 | 90% |
| medium | 39 | 90% | | variant | 25 | 88% |
| no_entity | 33 | 97% | | **two_entity** | 22 | **64%** |

Cost $1.42 per full run ($0.0060/request); latency median 2.1s, p95 8.0s.

**Decision: A5 is accepted at measured behaviour rather than at its original ≥95% gate.**
That bar was written in Phase 0, before the three-tier answer model and before a decline
could carry a clarifying question. What it was built to prevent — a card that confidently
answers the wrong question — occurs at 3.4%, and the false-positive test is 32/33. The
remaining shortfall is mostly honest declines, which cost a turn rather than mislead.

**Two caveats that the headline number hides, recorded so they are not rediscovered
later.**

*The held-out batch is materially worse.* Batch 5 scored 80.0% exact with **10.0% wrong**,
against 90.6% and 2.1% on batches 0–4. Every earlier batch has been read while diagnosing,
so 88.8% is a number partly measured on prompts the configuration has already seen. The
honest estimate of behaviour on genuinely new audio is closer to **80%**, on n=40. Whether
batch 5 is harder or the configuration is over-fitted to the earlier batches cannot be
settled without batch 6.

*Two-entity queries are the weak class at 64% (n=22)*, and this time it is not a
small-sample artifact — it held at 68% and 72% across earlier runs. STT mangles both names
and the corrector must recover both, so failure compounds. **Q4 should not assume the
single-entity number.**

**Phase 0 exit: A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ (gates Phase 3B) · A7 ◐ · A1 ⊘ retired
· A5 ✅ accepted at measured behaviour.** Phase 1 is unblocked.

### Survey outcome (0.5 / 0.7 — complete)

Source survey is **done**; see [ADR-0014](adr/0014-game-files-as-source.md). Structured data
comes from the game's own `.pak` files rather than community scraping, via MIT-licensed
tooling (`PalworldDataExtractor`, `cheahjs/palworld-save-tools`) plus FModel for level data.

| Assumption | Status after survey |
|---|---|
| A2 save parsing | **Confirmed, with a caveat** — see 0.3 below |
| A3 breeding ranks | **De-risked** — exception table exposed as a distinct dataset, corroborating the rank model |
| A6 unlocked tech | **CONFIRMED** — see 0.3 below |
| A7 licensing | **Narrowed** — risk now confined to the Q7 prose corpus; structured data is licence-clean |
| **A4 transform** | **Unchanged — the hard gate.** Level-data extraction plus in-game verification |

Target game version is **1.0.2**.

### Spike 0.5 outcome — headless pak extraction (working)

A CLI extraction pipeline is **operational**, with no GUI step. This matters beyond
convenience: a GUI in the ingestion path would be a permanent per-patch tax.

| Finding | Detail |
|---|---|
| Pak mount | **185,003 files** indexed from a single 40.5 GB `Pal-Windows.pak` |
| **Encryption** | **None.** Footer carries a zero encryption GUID and `bEncryptedIndex=0` — **no AES key needed**, removing a per-patch dependency on third-party key extraction |
| Pak format | v11, Oodle compression (same codec the saves adopted) |
| Mappings | `PalworldModding/UsefulFiles` `Mappings.usmap`, updated July 2026. usmap **v4** |
| Toolchain | .NET 10 + CUE4Parse `1.2.2.202608`. usmap v4 requires ≥ `1.2.2.202607`, which targets net10 only |
| World Partition | **9,977** generated cells under `PL_MainWorld5/_Generated_/`, named `CloseRange_L0_X{x}_Y{y}_DL0` — **cell names encode grid coordinates** |
| Extracted | `DT_BossSpawnerLoactionData` → **159 rows** with world coordinates and levels |

Sample extracted row:

```json
{ "SpawnerID": "yamijima_IceLand_pink_D_BOSS",
  "CharacterID": "BOSS_Horus_Water",
  "Location": { "X": -867560.9, "Y": -441338.22, "Z": 18640.152 },
  "Level": 66 }
```

World coordinate extent: X `-1,033,348 … 601,097`, Y `-733,420 … 575,683` (UE cm).

**A4 — CONFIRMED. Validation passed; the v1 hard gate on v1 is cleared.**
Stored in [`../data/coord_transform.json`](../data/coord_transform.json) as
`palworld-1.0.2-linear-axisswap-v2`, status **accepted**.

```
map_x = (world_y - 157818.3) / 458.7383
map_y = (world_x + 124238.1) / 458.7383
```

**Independent validation:** 7 landmarks held out entirely from the fit were read in-game
and compared against v1's predictions. **Worst error 3.0 map units, mean 1.92**, against a
10-unit threshold. This is the meaningful verdict — the points had no influence on the
parameters being tested.

Validation also surfaced a defect the fit alone could not: **`dy` was systematically
positive on all 7 points** (mean +1.69, sd 0.75). A consistent sign across widely separated
landmarks is a parameter offset, not reading noise. v2 corrects it — `offset_x` moves 500
world units (~1.1 map units) — and was refitted on all 11 points.

v2 residuals are consequently **not** an independent test; the refit consumed the validation
set. That is an acceptable trade because validation had already confirmed the *model form*,
making the refit a precision improvement rather than a fresh claim. About 13 landmarks
remain unread and still provide ongoing independent checks.

One mild outlier: point 2 (`5_2_island_iceblock_FBOSS_1`, residual 5.4) sits opposite the
systematic bias, so correcting the bias worsened it. Most likely read from further off the
spawn point. Worth re-reading; not currently harmful.

Practical scale: 1 map unit ≈ 4.6 m, so a 3-unit error is ~14 m — well inside visual range
of a node cluster.

**The axes are swapped** — map X derives from world Y and vice versa. This was the specific
risk flagged in [03-data-ingestion.md](03-data-ingestion.md) §3.1.1, and it is real: assuming
the obvious axis pairing would have produced confidently wrong coordinates everywhere.

The two axes were fitted **independently**, so a shared scale was a possible outcome rather
than a built-in assumption. They agreed to **0.16%** (458.93 vs 458.20 world units per map
unit). That convergence is the strongest evidence the model is correct — a wrong model does
not produce two independently-fitted axes that agree.

Worst fit residual is 4.0 map units, consistent with readings taken while standing *near*
a boss spawn point rather than exactly on it.

**Validation remains outstanding**, and matters: fit residuals only measure how well the
model reproduces its own inputs. The 20 disjoint `VALIDATE` landmarks stay unfilled until
confirmed, and `status` stays `provisional` until they are. Acceptance threshold: 10 map units.

Two `VALIDATE` rows sit in the World Tree region (points 8, 20) and one on an oil rig
(point 16); substitute from `all_boss_landmarks.csv` if unreachable.

Two easier sources were ruled out and are worth recording so they are not re-attempted:

- **Fast travel points** — `DT_RespawnPointInfo` holds spawn-region metadata
  (`ResourcesAbundant`, `PalAbundant`), not coordinates.
- **Player position from the save** — lives in `Level.sav` character blobs, behind the same
  stale `RawData` decoder that spike 0.3 found broken on 1.0.2.

**Then:** node placements come from the World Partition cells. The grid-coordinate naming
should constrain the search considerably rather than requiring all 9,977 cells to be parsed.

### Spike 0.3 outcome — save parsing (complete)

Run against a live 1.0.2 save. Both assumptions resolved.

**A6 — CONFIRMED.** The player save exposes exactly what Q6 needs:

| Field | Observed |
|---|---|
| `UnlockedRecipeTechnologyNames` | 118 entries, by tech name (`Workbench`, `PalBox`, `RepairBench`, …) |
| `TechnologyPoint` | 230 |
| `bossTechnologyPoint` | 33 |
| `PalStorageContainerId` / `OtomoCharacterContainerId` | container GUIDs → join into `Level.sav` |

Tech names are the natural join key to the `tech_tree.json` `tech_id`. Q6's candidate-set
arithmetic ([02-data-model.md](02-data-model.md) §4.2) works directly against this.

**A2 — CONFIRMED with a bounded caveat.** Two obstacles, both surmountable:

1. **Save compression changed to Oodle.** Palworld 0.6+ writes `PlM` (Oodle) where older
   versions wrote `PlZ` (zlib). `palworld-save-tools` 0.24.0 — the current PyPI release, and
   the current state of upstream `main` — handles **only** `PlZ`. Header framing is
   identical; only the codec differs. Resolved with a ~30-line shim over `pyooz` (module
   name `ooz`), an open-source Oodle-compatible decompressor. No proprietary DLL required.
2. **Some `RawData` sub-decoders are stale for 1.0.2.** `character.py` and `map_model.py`
   both fail with *"EOF not reached"*. These decode inner binary blobs, **not** the GVAS
   tree. With custom decoders disabled, `Level.sav` parses fully:

   | Structure | Count |
   |---|---|
   | `BaseCampSaveData` | 3 |
   | `CharacterSaveParameterMap` | 547 |
   | `ItemContainerSaveData` | 3,011 |
   | `CharacterContainerSaveData` | 5 |

   So base camps and the character roster are reachable now. Per-Pal detail (level, traits)
   lives inside the blobs those stale decoders handle. Updating them is bounded, well-understood
   work; actively maintained 2026 forks are the first place to look before writing our own.

**Consequences for the design**
- The save watcher ([01-architecture.md](01-architecture.md) §3.10) gains a native
  dependency on `pyooz`. Minor, but it is a compiled extension, not pure Python — note it in
  packaging.
- **Save-format drift is now a demonstrated risk, not a hypothetical one.** The compression
  codec changed between minor versions. The `SaveParser` interface and the
  "state unavailable" degradation path in [ADR-0005](adr/0005-save-file-player-state.md)
  are load-bearing, and the pinned parser version belongs in `/palintel status`.
- Q6 is fully unblocked. Q3/Q5 need per-Pal detail, so they depend on the blob decoders —
  sequence that work into Phase 3.

### Phase 1 data foundation — extraction complete

The Q1 dataset now exists. `tools/ingest/` holds the pipeline; `data/1.0.2/` the output.

| Output | Result |
|---|---|
| `lexicon.json` | **313 Pals** (286 in Paldeck), joined to `zukan_index`, plus resources |
| `resource_nodes.json` | **2,668 clusters** from 4,257 deposits — coal, ore, sulfur, quartz |

Full World Partition scan: **9,977 cells → 54,863 placements in 3.6 min, zero failures.**
Node actors are `BP_PalMapObjectSpawner_*_C`; Pal spawn zones are `BP_PalSpawner_Sheets_*_C`
(411 distinct classes, the Q2 payload, extracted but not yet processed).

**Three corrections the data forced:**

1. ~~**`crude_oil` is not a placed node.**~~ **Wrong, and it took until 2026-08-12 and a
   player standing on one to find out.** What was true: no `BP_PalMapObjectSpawner` class
   exists for it. What was written down: it is not placed. There are **185**
   `BP_LevelObject_OilField_C` actors, the blueprint names `CrudeOil` outright, and the
   game's item text says *"Obtained by installing a Crude Oil Extractor in an oil field."*
   Restored to `ResourceType`; see the 2026-08-12 session section.

   The original note, kept because the inference is the lesson:
   No spawner class exists for it in the overworld.
   Removed from `ResourceType` ([02-data-model.md](02-data-model.md) §3.1) — Q1 cannot
   answer "where is oil" the way it answers "where is coal".

2. **Single-link clustering chains badly.** Deposits strung along a cliff merged into one
   171-member "cluster" spanning a whole region — not a place a player can go. Replaced
   with leader clustering, which bounds cluster diameter by construction. Reported
   coordinates now **snap to a real deposit** rather than a centroid, which could
   otherwise land in a lake.

3. **Some actors store positions relative to a parent, not in world space** —
   **now fixed at the source.**

   Nodes scattered by a designer placement volume (`BP_BoxPlacementTool_*`) store
   `RelativeLocation` relative to that volume. Recorded verbatim they cluster near world
   origin, which maps to **(−344, 271)** — a plausible-looking spot with nothing there —
   producing a phantom 171-deposit coal hotspot. Precisely the confidently-wrong-coordinate
   failure this project exists to prevent.

   The first response was a stopgap: exclude everything within 2,000 world units of the
   origin and flag the residue `suspect_origin_artifact`. That cost **152 real coal
   deposits** and left the root cause in place.

   **The real fix**: every affected actor carries an `Owner` pointing at its placement
   volume, and those volumes have clean world transforms. The extractor now walks the
   owner chain and composes parent transforms, recovering true world positions. Results:

   | | before | after |
   |---|---|---|
   | placements near world origin | 383 | **0** |
   | coal deposits | 846 (152 dropped) | **998** |
   | largest coal cluster | 171 (phantom) | **9** |
   | owner chains resolved | — | 633, none deeper than one hop |

   The stopgap and the `suspect_origin_artifact` flag are gone. The **density guard**
   stays — it fails the build if any cluster exceeds 50 deposits within its ~110 m span,
   and it is what caught this in the first place.

   **Amended twice on 2026-08-10, and the second amendment reverses the first.** All 633
   owner-chain resolutions are in `L15_X0_Y0`, the cell holding dungeon contents, which
   first read as evidence that the fix had operated entirely on actors that are not on the
   map. That was wrong. Composing the owner chain puts those actors **back in world
   space**: they land on terrain 76.5% of the time against 79.4% for ordinary L0
   placements and 46.3% for the unresolved contents of the same cell. They are overworld
   nodes authored inside a placement volume, and the Phase 1 fix is exactly what makes
   them usable. Overworld coal is **497 deposits, not 998 and not 326**.
   See the card-artwork spike below for how it surfaced, and §3.1 of
   [03-data-ingestion.md](03-data-ingestion.md) for the rule.

**Still unpopulated:** `min_player_level` and `danger`. Both need wild Pal level data,
which comes from the `BP_PalSpawner_Sheets_*` actors already extracted.

---

## Phase 1 — Vertical slice: Q1 resource lookup (target: 2 weeks) — **closed 2026-08-10**

*Correctness, failure modes and real play all met; latency accepted at measured behaviour
on a 16-query sample and carried into Phase 2. See the exit section below.*

*"Hey Pal, where's the nearest coal?"* → card with coordinates, in under 2.5 seconds.

Deliberately narrow: **one** query class, **one** resource type, end to end.

**1.1 Data foundation**
- Ingest resource nodes for one resource type
- Calibrate the `min_player_level` rule against ~20 known nodes
- Build the resource-name lexicon
- Ship the validation suite alongside, not after

**1.2 Runtime skeleton**
- Long-lived process, Discord connection, config, structured logging
- Knowledge base loader with version reporting
- `/palintel status` — data version, save-parse state, provider health

**1.3 Input paths**
- Voice receiver with per-speaker streams
- Activation gate: VAD + wake word; endpointing
- **Text intake from the channel** — trivial once routing exists, and it is what makes the
  rest of the phase testable without a microphone

**1.4 Understanding**
- STT client with keyterm boosting
- Lexicon corrector
- Intent router with `find_resource_nodes` as the only registered tool
- Explicit decline path

**1.5 Answer + presentation**
- `find_resource_nodes` + unit tests
- Save watcher → `PlayerState` → dispatcher injection
- `ResourceNodeCard` template tuned for at-a-glance legibility
- Discord publisher with backoff

**Exit criteria**
- Voice p95 ≤ 2.5s and text p95 ≤ 1.5s over ≥ 30 real **answered** queries each, with
  decline latency tracked beside it and not graded ([00-overview.md](00-overview.md) §7)
- Zero fabricated coordinates across the eval set
- Every failure mode in [01-architecture.md](01-architecture.md) §8 produces its card
- **Used during a real play session without disrupting it** — the only test that matters

### Phase 1 progress — voice input, wake word, and save state (2026-08-09)

**Voice input is the local microphone, not a Discord voice channel.** Discord's DAVE
end-to-end encryption broke reception in py-cord
([pycord#3139](https://github.com/Pycord-Development/pycord/issues/3139)): the connection
succeeds, a sink attaches, and no audio ever arrives — a failure indistinguishable from a
wake word that never fires. Not fixable from this side. Output is still a Discord channel.
Amended into [ADR-0004](adr/0004-wake-word-activation.md) and
[ADR-0012](adr/0012-dual-input-channels.md), where the real loss lands: **party members
can no longer ask by voice.** `listening.py`, `wakeword.py` and `stt.py` were untouched by
the switch — the utterance buffer was already transport-agnostic — and the mic still suits
the detector better, since it delivers mono int16 in fixed blocks with no packet-boundary
remainder to carry. (The stronger claim first written here — that capture *is* the
detector's 16 kHz frame, so no resampling is needed — held only for the device this was
first run against. See the play-session findings below.)

**`hey_pal` trained and measured.** 30k synthetic samples via Piper, 50k steps, nine
compatibility shims documented in `tools/wakeword/train.py`. Scored against the 236 A5
recordings that actually open with the phrase:

| threshold | recall | non-wake-word clips firing |
|---|---|---|
| 0.1 | **91.9%** | 0/4 (max score 0.001) |
| 0.3 | 83.1% | 0/4 |
| 0.5 | 79.2% | 0/4 |

13 clips score below 0.05, so **94.5% is the ceiling for any threshold** — those are hard
failures, not near misses, which is why the curve is nearly flat above 0.1 and why the
default threshold is 0.1. For contrast, none of openWakeWord's pretrained models fire on
"hey pal" at all (`hey_mycroft` scores a literal zero); the phrase is "hey + one syllable"
and they are all trained on longer shapes.

*A measurement error worth recording.* The first run reported 77.9%, because the evaluator
assumed every recording opens with the wake word. Four do not — the `control` prompts are
bare Pal names — so they were scored as recall failures. The understatement is the smaller
problem: had those clips *fired*, they would have counted as successes while actually being
false positives. The label now comes from `prompts.json` rather than from assumption.

**The false-positive side is genuinely unmeasured.** Four negative clips and no continuous
room chatter cannot support a rate. It becomes measurable in use, which is what
`/palintel status` is for.

**`/palintel status` shipped** — ADR-0004's named mitigation, and overdue. A wake-word
false negative is silent, and "voice is broken" has four causes that feel identical to the
player. The breakdown separates them: no activations points at the mic or the model,
activations without transcripts means it is firing on noise, transcripts without answers
means routing. Mic overruns are counted too — dropped input frames present as a wake word
that intermittently misses. Events are memory-only; persisting them would mean writing
transcripts of everything said near the microphone to disk.

**Save watcher shipped — "nearest" now means nearest.** `PlayerState` was never populated,
so `find_resource_nodes` had been silently ranking by deposit count the whole time.
Measured against the real save, player at map (287, 623):

| | top answer | distance |
|---|---|---|
| Without save state | (321, 500) — 9 deposits | ~128 map units (≈590 m) |
| With save state | (300, 616) — 1 deposit | **15 map units (≈69 m)** |

The player transform comes from `Players/<uid>.sav` alone — no need for `Level.sav` or the
`RawData` blob decoders that are stale for 1.0.2. **Player level therefore stays `None` and
level gating stays off**, since it lives in exactly those blobs. The container handles both
`PlZ` (zlib) and `PlM` (Oodle); format drift is demonstrated, not hypothetical, so every
failure path keeps the last good snapshot rather than raising, and a torn read deliberately
does not consume the file's mtime so the next poll retries immediately.

**A1's retirement propagated** into `00-overview.md`, `01-architecture.md`, ADR-0006 and
ADR-0009, which still described tuning cards for an overlay that will not exist.

### Phase 1 progress — first play session, and what it broke (2026-08-09)

The real-play exit criterion was attempted, and the value of attempting it is that three
of the four things it found are invisible to a test run.

**The wake word never loaded.** `hey_pal` is not one of openWakeWord's pretrained names,
and `WakeWord` only resolved bare names against a `models_dir` that neither `mic.py` nor
`voice.py` passed — so the trained model sat in `data/wakeword/` while startup died on
`Could not find pretrained model for model name 'hey_pal'`. Resolution now defaults to that
directory, and checks the file exists before substituting a path, so `hey_jarvis` still
resolves the way it always did.

**Not every microphone does 16 kHz.** The configured device was the WASAPI entry for the
headset mic; WASAPI shared mode only opens at the rate Windows mixes the device at, so the
hardcoded 16 kHz request was a `PaErrorCode -9997` at startup. Nothing in the device name
says so — the *same physical microphone* appears once per host API, and its MME entry
converts happily while its WASAPI entry refuses. `mic.py` now probes for 16 kHz and falls
back to the device's own rate, resampling each capture block to exactly one detector frame
(3840 samples at 48 kHz, 3528 at 44.1 kHz). The resampler carries the previous block's tail
through the filter and discards that span of output; without it every 80 ms block gets edge
transients twelve times a second, worth ~5 dB of SNR. Measured ~60 dB against an ideal
16 kHz reference at 48/44.1/32 kHz.

**Declines were slower than answers**, ~2.6x, and it is thinking tokens rather than prose:
26 → 40 output tokens but 150 → 549 thought tokens. That is `ROUTING_POLICY` doing what it
was written to do — declining is the judgement it deliberately makes expensive, because
the policy names a false decline as the more common failure. The obvious lever is Gemini 3's
`thinkingLevel` (`thinkingBudget` is now a 400, and the surviving budget values are soft
hints — 128 requested, 277 spent). Swept over all 232 A5 utterances:

| level | exact | **wrong** | declined | median | p95 | $/req |
|---|---|---|---|---|---|---|
| default | 88.8% | **3.4%** | 20.7% | 2136ms | 8340ms | 0.0060 |
| low | 87.1% | **4.3%** | 21.6% | 1613ms | 4513ms | 0.0043 |
| minimal | 88.8% | **8.2%** | 15.1% | 1116ms | 1411ms | 0.0031 |

**Rejected both.** `minimal` reads as free — same exact accuracy, p95 down 6x, half the
cost — and it doubles the wrong-entity rate. The mechanism is visible in the decline column:
thinking less makes the model less willing to decline, so eleven queries the default
declined now get answered confidently and wrongly ("where can I find Leithbunk" → Lifmunk
rather than Leezpunk; two hallucinate entities into prompts that name none). That is the
failure [ADR-0007](adr/0007-entity-lexicon-boundary.md) refuses to ship, traded for a second
of latency. `low` is simply worse than default on accuracy, and its `high` twin measured
nearly identical thought tokens, so there is no finer setting hiding between them.

**The sweep's real finding was the timeout.** Every run had requests hitting the 120s
ceiling and returning "unreachable" — two minutes of a bot that looks hung, on a query
budgeted at 2.5s — plus stragglers at 56-64s. Both hosted backends now bound requests at
8s (Claude had no timeout at all). 8s rather than the 4s first proposed, because the data
overruled the guess: 4s cuts 20 correct answers, 8s cuts 5, and all five had already blown
the budget threefold. **This is not a latency saving** — nothing recovers a 60s query — it
is a bound on the worst case. A transient failure falls through to `StubRouter` via
`FallbackRouter`. Only *transient* declines fall through: a considered decline is passed on
untouched, because the stub knows strictly less than the model did and re-deciding on less
is how a "no" becomes a confidently wrong "yes".

> **Corrected below.** As first written this fallthrough could not rescue anything once the
> fast path shipped, and the claim that it "answers a clear Q1 resource query outright" was
> false for the whole time it stood here. See *A backstop that could not back anything up*.

**`routing_gemini.py` was importing the local backend's system prompt** — the one that says
"your only output is one JSON object… otherwise pick `decline`" — while using real function
calling. Gemini followed it faithfully, three ways: it called a function named `decline`
(unregistered, so a hard error at the dispatcher), it emitted `find_resource_nodes` with no
`resource` (a `TypeError` out of `execution`), and it put bare `{}` and fenced JSON in the
text half of a decline, which rendered **verbatim on the player's card**. `routing.py` had
always documented that the output-format sentence is the one thing that differs per backend;
this import was the exception that proved it. Re-measured on the full A5 set to confirm the
correction did not move the baseline:

| | exact | wrong | declined | p95 |
|---|---|---|---|---|
| local-grammar prompt (all earlier runs) | 88.8% | 3.4% | 20.7% | 8340ms |
| function-calling prompt | 89.2% | 3.4% | 20.7% | 6673ms |

Unchanged, which is the result the fix wanted — the +0.4pp is a single query and the
2-regressed/3-improved churn is ordinary run-to-run variance. **The 88.8% headline in the
A5 tables above was therefore measured under the wrong prompt, and survives it.** The
pre-fix detail is kept as `router_gemini-3.6-flash_localprompt.json` rather than
overwritten. Each malformed shape is also handled defensively regardless of prompt: a model
can always emit a bad call, and neither a crash nor raw JSON on a card is an acceptable
answer to that.

**Still unmeasured:** the latency exit criterion. These are router numbers against recorded
transcripts, not end-to-end voice p95 with STT and rendering in the path, and the 2.5s bar
is written against the latter. `thinking_level` is plumbed through and defaults to off — a
measured knob, deliberately unused.

### Phase 1 progress — the Q1 fast path (2026-08-09)

The model round trip is a ~2s median and Q1's whole budget is 2.5s, so on a plainly-phrased
resource query the round trip *is* the latency problem. Nothing in the model tuning could
fix that — the `thinkingLevel` sweep above established there is nothing to buy there — so
the answer is not to make the call, which
[ADR-0009](adr/0009-v1-vertical-slice.md)'s single-tool slice makes unusually safe:
`StubRouter` answers from the same knowledge base and the same lexicon, with no model in
the loop to fabricate anything.

**Measured before building it, on the A5 transcripts, at zero API cost.** Coverage is scored
on the 15 prompts a Q1 build can answer; precision is scored across all 232, because the
way a keyword router fails is by claiming queries from *other* classes and those live
outside Q1:

| cue set | Q1 answered | wrong resource | claimed outside Q1 |
|---|---|---|---|
| standard (the original list) | 8/15 = 53% | 0 | 0 |
| proximity (`near`, `nearby`, `around here`) | 10/15 = 67% | 0 | 0 |
| wide (adds `i need`, `get me`, `any`) | 11/15 = 73% | 0 | 0 |

On every query the stub claimed, the model had independently made the same call. It defers
"do I have enough sulfur for this" — which names a resource and is not a location question,
and which the model also declined — so the cue gate is discriminating, not just filtering
noise. Live, the two `wide`-only phrasings go from ~1.8s to ~0.1s.

**`wide` is live behind a flag, and the flag is the point.** 15 prompts is a thin basis for
"zero precision cost", and `wide` is the only width that guesses at intent rather than
naming a place — it is also the most exposed when Phase 2 registers `find_pal_spawns` and
there is finally another class to steal from. `router.cues = "proximity"` is the same trade
without the intent guesses, at two queries of coverage; `router.fast_path = false` restores
model-only routing exactly. The cue width appears in the router's name, so it reaches
`/palintel status` and every routing log line: a fast path that quietly widened would be
indistinguishable from the model getting worse.

One stub instance serves both the fast path and the transport backstop, so the width cannot
diverge between them — the same query being claimed or deferred depending on whether the
network happened to be up is not a behaviour worth being able to express.

**The session will now produce the numbers.** Per-stage timing is recorded into the
activity log and reported by `/palintel status`: end-to-end p50/p95 for voice and text
against their budgets, plus an STT / route / post breakdown so a miss says *where* it went
rather than only that it went. Two details are load-bearing. The voice clock starts at end
of speech, which is **700 ms before the buffer closes** — the endpointing hangover is time
the player spends waiting and the budget owns it, so `Utterance.ended_at` unwinds it rather
than letting the pipeline start its stopwatch late and quietly bank a quarter of the budget.
And the card refuses to grade a thin sample: under 30 queries it shows `⏳ n/30` instead of
a tick, because the criterion says "over ≥ 30 real queries" and a p95 over six is not a p95.

### Phase 1 progress — the second session, and what typed text was hiding (2026-08-09)

Twenty-one voice queries, then six more. **Voice p50 3.2s, p95 4.8s against a 2.5s bar** —
and the breakdown immediately contradicted the prediction made a few hours earlier:

| stage | p50 | predicted |
|---|---|---|
| STT | **0.38s** | "the likeliest place for the budget to go" |
| route | **1.70s** | near zero, because the fast path would cover the median |
| post | 0.22s | — |

STT was the smallest term in the budget, not the largest. Routing was 4.5x bigger, which
means **the median query was still going to the model** — the fast path was not firing.

**Reading the actual transcripts is what found it**, and required building `/palintel
recent`: the activity log had been storing every query's text and routing time since the
instrumentation went in, and nothing displayed them, so the data needed to diagnose a
missing answer existed and was unreachable from Discord, which is where the person asking
is standing. Six verbatim transcripts, every one **answered correctly**:

| heard | why it went to the model |
|---|---|
| "where's the nearest **goal**?" | coal ranked **0.75**, floor is 0.78 |
| "find me a **North Spot**" | ore 0.57, top candidate `Finsider` |
| "**gimme** some quartz" | quartz **1.00** — the cue list knew "get me", not the contraction |
| "we're sitting near **a store**" | ore 0.75, tied with a Pal at 0.75 |

Two distinct causes, and conflating them would have produced the wrong fix. The mangled
nouns are the *architecture working*: the corrector ranks, the stub defers because it
cannot reason, and the model recovers the entity from sentence context — exactly
[ADR-0016](adr/0016-entity-resolution-in-router.md). Dropping `MIN_CONFIDENT` to 0.75 to
catch "goal" would also let the stub answer "a store" on a coin-flip between ore and a Pal.
`gimme` was a pure vocabulary miss on a perfect 1.00 entity, and is now in the cue list.

**The real fix was one line in `stt.py`, and the bug was hiding inside `sorted()`.** The
hotword list was `sorted(canonical_names)`, and the resources are the only lowercase
entries — so ASCII put all 313 capitalised Pal names ahead of the four nouns Phase 1 can
answer about. The entities the whole phase is built on carried the *least* decoding bias.
Re-measured over the 19 recorded resource clips, scored by whether the entity clears
`MIN_CONFIDENT` afterwards — the exact condition the fast path tests:

| hotwords | resource clips | pal clips |
|---|---|---|
| none | 15/19 | 38/60 |
| all, sorted (what shipped) | 16/19 | 44/60 |
| resources only | **19/19** | 35/60 — buys Q1 by wrecking Phase 2 |
| **resources first** | **19/19** | 42/60 |

"goal", "a store" and "an over spot" all transcribe correctly with the resources hoisted,
which takes them to 1.00 and onto the fast path. Resources-first is the only option that
reaches 100% on Q1 without abandoning the Pal names Phase 2 needs, and it is not free:
2 of 60 Pal clips regressed, which on that sample is as likely noise as signal and wants
re-measuring when a Pal tool actually depends on them.

**Two diagnostic holes closed on the way.** `dispatch` discarded the Future from
`run_coroutine_threadsafe`, so any exception after transcription — posting the card
included — vanished until garbage collection; and "answered" was recorded *before* the
send, so a card built and never delivered still counted as an answer. Between them, the
one failure the player actually experiences (asking and getting nothing back) was
invisible in the status card that exists to explain it.

**The latency criterion now grades answers and tracks declines beside them**
([00-overview.md](00-overview.md) §7). Graded together, the p95 landed on a decline
whatever the answer path did — with 30 queries the p95 is the second slowest, so two
declines decided it — which made the bar a measure of how often the system declined
rather than how fast it answers. Un-graded is not untracked: the decline p50/p95 sits on
the same card, because a slow decline is still the player waiting.

This is not a fudge, and the first simulated card proves it: with a third of answers still
model-routed, voice p95 sits at 3.3s on answered queries alone and still fails. Excluding
declines removed a distortion, not the problem.

### Phase 1 progress — a backstop that could not back anything up (2026-08-09)

The third session put the fast path on the median: **route p50 0.11s** against 1.70s the
session before, voice p50 **1.5s** inside a 2.5s bar, text p50 0.3s. It also exposed a
wrapper that had never been able to do its job.

**`FallbackRouter` could not rescue a single query.** `FastPathRouter` asks the stub first,
so anything reaching the model is by definition something that stub already declined — and
on a timeout the fallthrough asked *the same deterministic router the same question* and
got the same decline. Sharing one instance had been written up as the careful choice, on
the grounds that the cue width could not then diverge between the two roles. It was
actually what made the safety net decorative, and the docstring and roadmap both claimed
otherwise for as long as it stood.

The fix is asymmetry, because the two roles face different alternatives. The fast path
preempts a *working* model and must stay strict — everything it claims is a query the model
never sees. The backstop runs only when the model did not answer at all, so its alternative
is not a better answer but nothing.

**The first attempt at that was wrong, and the measurement said so.** Lowering
`MIN_CONFIDENT` from 0.78 to 0.55 recovered exactly **one** query in 232, which looked like
proof that permissiveness does not help. It was proof the knob was wrong: one constant
gated both the Pal guard ("the top candidate is confidently a Pal, so this is a Pal
question") and resource acceptance, and lowering it made the second looser while making the
first *tighter* — a Pal at 0.71 started clearing the bar and triggering the guard. Split
them, hold the guard at 0.78, and sweep the resource floor alone:

| resource floor | Q1 right | wrong | claimed outside Q1 |
|---|---|---|---|
| 0.78 | 12 | 0 | 0 |
| **0.68** | **12** | **0** | **0** |
| 0.64 | 13 | 0 | 3 — "can I get Zendelord" → ore |
| 0.60 | 13 | 0 | 4 — also answers a no-entity prompt |

0.64 is where confidently wrong cards start appearing on Pal queries, which
[ADR-0007](adr/0007-entity-lexicon-boundary.md) refuses to ship whether or not the model
was reachable. 0.68 recovers two of the three mangled transcripts from the session —
"nearest **goal**" and "near **a store**", both heard as coal and ore at 0.75 — and none of
the wrong ones. Chosen by where wrong answers begin, not by where coverage stops improving.

**The cue list also gained the phrasings the session actually used.** `/palintel recent`
showed the two slowest *answered* queries were cue misses on clean entities: "can I get coal
at this level" and "what's the best place to farm quartz", both 1.00 on the resource, both
paying a full model round trip. Q1 coverage 11/15 → 12/15, still nothing claimed outside
Q1. `gather`, `harvest`, `stock up` and `pick up` were measured, added nothing, and are
deliberately absent. None of these would have been guessed from typed text — the queries
that miss are exactly the ones nobody thinks to write down.

### Phase 1 exit — closed, with latency accepted at measured behaviour (2026-08-10)

**Phase 1 exit: correctness ✅ · failure modes ✅ · real play ✅ · latency ◐ accepted on a
thin sample, carried into Phase 2 as a watch item.**

| criterion | outcome |
|---|---|
| Zero fabricated coordinates | ✅ structural, and asserted at load |
| Every §8 failure mode produces its card | ✅ for the modes Phase 1 owns |
| Used during a real play session without disrupting it | ✅ four sessions |
| Voice p95 ≤ 2.5s, text p95 ≤ 1.5s, ≥ 30 answered each | ◐ **not formally met** |

Final measured shape, best session:

| | p50 | p95 | n | budget |
|---|---|---|---|---|
| Voice, answered | **1.4s** | 4.4s | 16 | 2.5s |
| Text, answered | **0.3s** | 0.5s | 16 | 1.5s |
| Voice, declined | 4.6s | 8.3s | 6 | not graded |
| stages | stt 0.39s · route **0.11s** · post 0.19s | | | |

**Why this is accepted rather than passed.** The criterion asks for ≥ 30 answered queries
and the best session reached 16, at which point `int(16 × 0.95)` is the last index — the
reported p95 *is* the maximum, one query, not a percentile. Both medians sit comfortably
inside budget and decompose exactly as a fast-path query should (0.70 hangover + 0.39 STT +
0.11 route + 0.19 post = 1.39s). The number that fails is a statistic the sample cannot
support, and collecting fourteen more queries to settle a bar that Phase 2 is about to
change would be measuring the wrong thing. Same call, and the same wording, as
[A5 at the Phase 0 exit](#a5-verdict--measured-in-phase-1): accepted at measured behaviour,
with the caveat recorded rather than rounded away.

**What is genuinely established.** Routing left the critical path: `route p50 0.11s`,
against 1.70s two sessions earlier. In the last clean window every answered query took the
fast path and every model call was a decline — the shape the design was aiming at. And
across four sessions and ~90 real queries, **not one wrong answer**: every mangled noun
either recovered correctly or declined honestly, which is the criterion this project was
actually organised around.

**Watch, in Phase 2.** The latency picture is expected to get *worse* before it gets
better, and predicting that now is cheaper than being surprised by it: `find_pal_spawns`
adds a query class the stub cannot answer at all, so a larger share of traffic returns to
the model and the fast path's share of the median drops. Phase 2 is therefore the honest
place to re-measure this, with a bigger sample and a realistic query mix, rather than
grinding out fourteen more resource queries against a build that is about to change.

---

## Phase 2 — Breadth and conversation: Q2 (target: 2 weeks) — **closed 2026-08-10**

*Intent, entity and follow-up criteria all met on the delivered classes. End-to-end
latency is carried into Phase 3, unmeasured for a second phase; the A5 entity gate
remains where Phase 0 left it. See the exit section below.*

- Full Paldeck + spawn ingest; complete lexicon generation
- All resource types for Q1
- `find_pal_spawns` + card
- **Multi-tool routing** — the first point requiring disambiguation between classes. Grow
  the eval set to ≥ 50 utterances spanning both.
- **Re-measure the Q1 fast path against the grown eval set** — this is the phase that can
  invalidate it. Its precision was measured with only one tool registered, so "claimed
  nothing outside Q1" was scored when there was no other tool to claim *for*. Registering
  `find_pal_spawns` gives a keyword matcher its first real chance to be confidently wrong,
  and `router.cues = "wide"` is the width to re-justify or step back first.
- **Settle the Phase 1 latency criterion**, carried forward from a 16-query sample where
  the reported p95 was the maximum rather than a percentile. Expect it to look *worse*
  first: `find_pal_spawns` adds a class the stub cannot answer, so more traffic returns to
  the model and the fast path's share of the median falls. The question to answer is not
  "does the old number hold" but **what fraction of a realistic two-class mix the fast path
  can still carry** — and if the answer is "much less", that is an argument for a Q2 fast
  path, not for relaxing the bar.
- **Re-measure the STT hotword order**, whose reordering cost 2 of 60 Pal clips. At that
  sample it is as likely noise as signal, and it stops being ignorable the moment a Pal
  tool depends on those names.
- **Conversation memory** ([ADR-0013](adr/0013-conversation-memory.md)) — follow-ups now
  have something to refer back to
- Multi-speaker attribution in a shared channel

**Exit:** ≥ 90% intent accuracy across both classes; ≥ 95% entity extraction; follow-up
resolution correct on a 20-case eval set.

### Phase 2 progress — the spawn dataset, and the source that disagreed with itself (2026-08-10)

**The data gate is open.** Q1's placements already carried 13,895 `pal_spawn` actors, but
nothing anywhere said which Pal `BP_PalSpawner_Sheets_green_K_C` rolls — that lives in the
sheet blueprint's own `SpawnGroupList`, a different asset tree the cell scan never touches.
A second `PakExtract` mode reads all 468 of them: species, level range, weight, and
day/night per group. **All 411 placed classes resolve**, none needed the inheritance
fallback the extractor carries, and of 372 distinct spawn ids exactly one — a human NPC —
fails to map to the lexicon.

| | |
|---|---|
| Spawn areas | **19,272** across **271** Pals (19,118 normal · 123 alpha · 31 predator) |
| No overworld spawn | 42 Pals, listed explicitly |
| Cluster radius | 25 map units (~115 m) |

**The 42 are a feature, not a shortfall.** They are the tower pairs, the raid bosses, the
Terraria collab, the dungeon-only bats and the Sakurajima variants — `Katress Ignis`
appears only in `dungeon_Sakurajima` sheets, `Mau` only in `dungeon_grass`. Shipping the
list is what lets the answer be *"Jetragon does not spawn in the overworld"* rather than
*"not found"*, which are different claims and only one of them is true. A test asserts the
two sets partition the Paldeck, so there is no third state to fall into.

**Two sources for alphas, and neither contained the other.** The sheet actors and
`DT_BossSpawnerLoactionData` were expected to agree; checking rather than assuming found
that the table knows 16 species the actors do not (Penking, Wixen, Blazehowl…) and the
actors know 3 the table does not (Necromus, Broncherry Aqua, Ribbuny Botan). Where they
overlap on 74 shared spawners they agree to a **median of 0.0 map units, p90 0.1** — which
is what makes merging safe: at a 25-unit radius a duplicate collapses into one area and a
genuine second location survives. Two do survive and should: Caprity Noct and Foxparks
Cryst each have a low-level main-island alpha in the table and a separate level-50s one on
Feybreak in the sheets, 1,400 units apart. Both transforms cancel out of that comparison,
so what it actually validates is the actor extraction and its owner-chain composition.

**PvP arena sheets are excluded, and that is a judgement rather than a cleanup.** They are
placed 1,113 times across the whole map carrying the common early species — 83% of every
Rushoar spawn point, 73% of every Chikipi — so leaving them in flattens the density signal
that makes "nearest" mean anything, for exactly the Pals a new player asks about. It costs
no coverage: **no Pal reaches the dataset through a PvP sheet alone.** Whether they are
live during normal play is *not* established here; `--include-pvp` restores them, and it
is a validation item, not a settled question.

**Two anomalies worth naming.** The shipped game data has two entries with `Level` above
`Level_Max` (a Pengullet typo in two snow sheets); the ingest sorts the pair and reports it
rather than emitting a record whose minimum exceeds its own maximum. And `Mimog` holds
10,116 spawn points because the mimic sits at weight 2–4 in 139 different sheets — density
alone would call it the commonest Pal on the map, which is why `encounter_share` ships
beside the point count and reports it at ~1%.

**One assertion was wrong before the data was.** The first build aborted on 735 areas
"exceeding the cluster radius". Leader clustering bounds members to the radius from the
*seed*, but the reported coordinate is the member nearest the *centroid*, so the real
bound is the diameter. The check now asserts what the algorithm actually guarantees.

**Still to do in this phase:** none of this has been checked in-game. The resource ingest
had ~20 nodes read off a real map before it was trusted and this has had one — the desert
Anubis alpha, at (-134, -94), which a test now pins.

### Phase 2 progress — two tools, and the imprecision the Pal guard was hiding (2026-08-10)

`find_pal_spawns` is registered, dispatched and rendered. One registry serves all three
backends (`routing_anthropic.registry`), so the tool cannot be live for one router and not
another — which would make their measurements incomparable while still looking like a fair
test. The A5 harness stopped injecting its own copy at the same time.

**Encounter kind and time of day are read off the utterance, not asked of the model.**
Strict tool use expresses an optional parameter as a nullable type that is still
`required`, which has no clean form for an enum and would need validating against both
hosted APIs; and `StubRouter` already lifts "level 30" out of the utterance with a regex,
so deriving them in the dispatcher means the fast path and the model path agree by
construction rather than by luck. The tool's parameter list is therefore exactly what the
A5 runs measured. `boss` is deliberately *not* a trigger word: players call tower bosses,
raid bosses and field alphas all "boss", and only the last is in this dataset, so matching
it would answer "where's the Zoe boss" with a field location for a tower fight.

**Re-measuring the fast path was the point of the phase, and it found something.** Scored
over all 240 A5 transcripts at zero API cost (`tools/eval/score_fast_path.py`), with the
second tool live:

| cue set | Q1 right | Q2 right | claimed outside both classes |
|---|---|---|---|
| standard | 10/18 | 23/49 | 0 |
| proximity | 12/18 | 23/49 | 0 |
| wide | 14/18 | 23/49 | **9** |

Phase 1 recorded "nothing stolen at any width" and named `wide` as the entry most likely
to move once there was another tool to claim for. It moved, on exactly that entry: all
nine thefts were the intent guesses — `any`, `i need`, `can i get` — firing on a Pal name.
*"Is Pierdon any good for logging"* and *"do I need a better spear for Mereth"* became
spawn cards. **The imprecision was always there; the Pal guard was absorbing it**, because
a confidently-matched Pal used to decline unconditionally, and registering the tool turned
those declines into confident answers.

The fix is not to drop `wide`. Each of its entries was earned by reading a real *resource*
query off `/palintel recent`, and none was ever justified for Pals. Gating the Pal branch
on the narrower `proximity` set keeps `wide`'s 14/18 on Q1 and takes theft from 9 to **0**.
Stepping back to `proximity` wholesale would have cost two Q1 answers to fix a Q2 problem.

**The Pal floor needed to be higher than the resource floor, and the data said where.**
Four resources against 313 Pals: the ranker's top candidate for a mangled Pal name is a
much weaker signal, the same asymmetry the STT hotword work found (19/19 resource clips
clearing the bar against 42/60 Pal ones). Swept at `proximity`:

| pal floor | Q2 right | wrong | Q1 wrong |
|---|---|---|---|
| 0.78 | 24 | **2** | **1** |
| **0.85** | **23** | **0** | **0** |
| 0.90 | 17 | 0 | 0 |
| 0.95 | 14 | 0 | 0 |

`PAL_CONFIDENT = 0.85` costs exactly one Q2 answer and removes every wrong card; above it
coverage collapses for no correctness gain. Chosen by where wrong answers begin, the same
rule as `BACKSTOP_CONFIDENT`. The answer it costs is not lost, only slower — it goes to the
model, which has the sentence context the ranker lacks ([ADR-0016](adr/0016-entity-resolution-in-router.md)).
At 0.78 the stub answered *"where can I find Banner and Cryst"* with Rayhound Cryst.

**A measurement error worth recording, because it flattered nothing and still mattered.**
The first scoring run classified query class with a regex over the *transcript* — which
scores STT's own mangling — and labelled "can I get coal at this level" as out-of-class,
counting its correct answer as theft. That phrasing is one Phase 1 deliberately added to
the cue list after reading it in a real session. Classification now comes from the
generator's clean text, and the resource side needs no allowlist at all: of the 19
resource-entity prompts, 18 ask where to get the stuff in eighteen different phrasings,
and the one exception is the inventory query Phase 1 had already named ("do I have enough
sulfur for this"). Enumerating the other eighteen would have been fitting the labels to
the router.

**One design tension left open.** A query naming a Pal with an element variant renders two
cards — Chillet *and* Chillet Ignis — per the variant-family design in `pipeline.MAX_CARDS`
and `Lexicon.same_family`. That reads as over-answering next to `ROUTING_POLICY`'s "never
list variants, alternatives, or runners-up". The two are arguably about different actors
(the policy stops the *model* hedging; this is a co-named entity STT cannot separate), and
about 17% of Pals are in a two-member family, so it is not rare. Left as documented rather
than reversed unilaterally, and worth settling in a play session.

**Still not measured:** the STT hotword re-check, end-to-end latency on a two-class mix,
and everything downstream of them. All three want a real session, not a harness.

### Phase 2 progress — all resource types, and what hand-mapping had got wrong (2026-08-10)

Q1 went from 4 resources to 18, and the interesting part is that the expansion was not the
point — deriving the mapping was, and it found a defect in shipped Phase 1 data.

**`CLASS_TO_RESOURCE` was six entries chosen by reading blueprint names, and two of the
six were wrong.** `BP_PalMapObjectSpawner_SkyIslandOre_C` yields **Soralite** and
`_WorldTreeOre_C` yields **Paloxite**; both shipped as `ore` for the whole of Phase 1, so
306 clusters told a player they had found ore when they had not. `_RockIron_C` would have
been guessed as iron — it yields Pure Quartz. `_RockStone18_C` yields **Chromite**, which
nobody would have guessed from the name at all.

The mapping is now derived, and the chain is entirely in the data:
`spawner CDO → MapObjectId → master table → map object → DropItems[].StaticItemId`. What
counts as a locatable resource is the game's own item category rather than an opinion —
`MaterialOre`, `MaterialStone`, `MaterialWood`, `FoodVegetable` — which admits the mined
and gathered materials and excludes the stat lotuses, Dog Coins and Kinship Peaches. The
derivation is shared with the lexicon build (`tools/ingest/_resources.py`), because the
two cannot read each other's output and a resource in one and not the other is either a
query resolving to an entity with no data or a node nobody can name. A test asserts they
agree, with `crude_oil` as the single deliberate exception.

| | before | after |
|---|---|---|
| Resources | 4 | **18** (+ crude oil, recognised but unplaced) |
| Clusters | 2,696 | **10,119** |
| Deposits | 4,635 | 28,933 |

**`min_player_level` and `danger` are populated**, closing the Phase 1 known gap — the
spawn ingest is what unblocked them. The rule is [03-data-ingestion.md](03-data-ingestion.md)
§5's, published on the dataset as `local-wild-pal-level-v1` so an answer stays traceable
to the rule that produced it.

**One departure from the written rule, forced by the data.** §5 says
`max_local_wild_pal_level`, and the literal maximum does not survive contact: in the level
1–7 starting area a Mammorest spawns on a **1% roll** at level 33–35, so the max makes the
beginner zone a level-35 region. It rated 65% of the map "high" danger with a median
gating level of 44. Weighting each nearby area by its expected encounter rate and reading
p90 — the hardest *common* encounter, not the rarest one — fixes it:

| zone | max | p90 |
|---|---|---|
| starter | 35 | **7** |
| desert alpha | 53 | 42 |
| volcano | 56 | 56 |
| Feybreak | 72 | 68 |

The rule is **uncalibrated**: §5 asks for ~20 nodes of known difficulty read in-game, and
that has not been done. It is checked for self-consistency and against those four
reference zones, which is not the same thing, and it is recorded as a known gap.

**A fail-closed check had to be corrected rather than satisfied.** The build aborted on
two red-berry clusters "exceeding 50 deposits — coordinates collapsing to a point". They
were not: 61 bushes at 61 distinct coordinates, median 7.8 map units from the centre — a
real thicket. The threshold was calibrated on rock and the check was testing count when it
means to test collapse. It now requires density *and* near-zero spread, which is the
actual signature.

**Eighteen resources broke a card that four had not.** A decline says "I can currently
find: …", and alphabetically that opened with Ancient Bark, Ancient Bone and Ancient Lava
— seven clusters each, on Feybreak, nobody's question. The list is now ordered by how much
data backs each resource and capped at six with "and N more".

### Phase 2 progress — conversation memory (2026-08-10)

[ADR-0013](adr/0013-conversation-memory.md) shipped at its stated defaults: 4 turns,
5 minutes, per user, in-process only. **22/22 on the follow-up eval set**
(`tools/eval/score_followups.py`), which is the phase's "follow-up resolution correct on a
20-case eval set" criterion — met on the deterministic router, not yet on the model.

**The eval has two columns and the second is the one that matters.** Twelve cases are
coverage: does the referent resolve. Ten are *negative* — cases where memory must **not**
fire, because the question is fresh, or opens like a follow-up but carries its own verb,
or has nothing left to refer to. A run that scores 12/12 on the first and loses the second
has made the system worse, since a stale referent produces a card that looks entirely
authoritative. Both columns are clean, and the single-turn fast path is unchanged at
14/18 · 23/49 · 0 stolen, so memory costs nothing when there is nothing to remember.

**Only answered turns are stored, and only resolved entities.** A decline resolved
nothing, so storing its best-guess candidate would manufacture a referent — the exact
failure the ADR warns about. `"where can I find Chillet"` → `"what should I research
next"` (declines) → `"where's the closest one"` correctly reaches back past the decline to
Chillet.

**The inheritance rule took three attempts, and driving a real conversation found each
one.** What a follow-up may borrow from the previous turn is the *verb*, never the entity:

- `"and coal?"` after a Pal query is a **resource** question. Matching the remembered tool
  instead of the named subject answered it with the Pal again.
- `"how about breeding Anubis"` opens like a follow-up and carries its own verb. Lending
  it the previous turn's verb answers a breeding question with a map location.
- `"and Banner and Cryst?"` names something the ranker cannot place. Falling back to the
  remembered entity answered a Pal question with the previous turn's coal.

All three are the same rule: strip the opener, the function words and the named entity,
and look at what is left. Nothing left means elliptical — inherit. Modifiers only
(`alpha`, `at night`, `the closest one`) — inherit the entity too. Anything else is
content this router cannot place, and the honest move is to defer to a model that can read
the sentence.

**A tokenisation bug hid inside that rule for one commit.** `STOPWORDS` holds `where`, not
`where's`, so tokenising with the apostrophe attached made `"where's the closest one"` look
like it carried content, and every anaphoric follow-up became a restatement request.

**Voice follow-ups are shared, and that is an input limitation rather than a choice.** The
local microphone cannot tell two people apart, so the voice path keys memory on the
literal `"voice"` while each Discord user's typed follow-ups are their own. Multi-speaker
attribution is the phase item that would fix it.

### Phase 2 progress — the hotword re-check, and a "noise" that was signal (2026-08-10)

Phase 1 hoisted the resources to the front of the STT hint list, recorded that 2 of 60 Pal
clips regressed, called it "as likely noise as signal", and left it to be re-measured when
a Pal tool depended on those names. Re-measured over **185 clips** with `find_pal_spawns`
live, scoring by whether the expected entity clears the floor the fast path tests — 0.78
for a resource, 0.85 for a Pal:

| variant | resource | pal |
|---|---|---|
| no hints | 15/19 | 83/166 |
| all, sorted | 16/19 | 100/166 |
| **all resources first** (Phase 1's choice) | **19/19** | **92/166** |
| pals first | 16/19 | 100/166 |
| **core resources first** | **19/19** | **101/166** |
| core + stone/wood/paldium | 19/19 | 97/166 |

**The 2-clip regression was signal. On 166 clips it is 8.** But the cause was not hoisting
resources — it was hoisting *nineteen* of them. The set grew from 5 to 19 when Q1 widened,
and pushing fourteen extra strings ahead of 313 Pal names is what displaced them. Hoisting
only the five the recorded set exercises is **strictly better than every other ordering
measured, on both classes at once**: 19/19 and 101/166.

Hoisting is a budget. Each entry at the front costs accuracy behind it, at roughly one Pal
clip per added resource.

**Two null results worth keeping.** `sorted` and `pals_first` produce **byte-identical**
transcripts across all 185 clips, which confirms Phase 1's diagnosis exactly: ASCII
sorting was silently a pals-first list. And feeding display names instead of canonical ids
(`Hexolite Quartz` rather than `hexolite_quartz` — ten of the nineteen carry underscores)
changed 82 of 185 transcripts and moved **neither column**, so the ids stay.

**Known gap, deliberately not closed.** Stone, wood and paldium have no recorded clips and
are almost certainly common in real play. Hoisting them costs a measured 4 Pal clips for
an unmeasured gain, so they stay unhoisted until there are clips to settle it with —
trading a measured loss for an assumed benefit is the move this project keeps refusing.
A miss here is not a wrong answer either way; the floors still hold, and the cost is a
model round trip.

### Phase 2 progress — speaker attribution, and an item the pivot had already answered (2026-08-10)

"Multi-speaker attribution in a shared channel" was written when voice arrived over
Discord, tagged per user. The DAVE pivot removed that channel, and with it most of the
item: `SpeakerStream` already does per-speaker attribution and is dead code held for the
day reception is fixed, the text path has always keyed on the Discord display name, and
**party members cannot reach a local microphone at all**. What remained was not detection
but identity — and one live bug.

**Conversation memory had broken ADR-0012's cross-channel promise, and nothing caught
it.** Memory is per person; the text path keys on the display name and the voice path had
no identity to key on, so it used the literal `"voice"`. The same human's spoken question
and typed follow-up therefore landed in two separate threads:

```
voice:  "where can I find Chillet"     -> answered, remembered under "voice"
text:   "what about the alpha?"        -> "I've lost track of what that refers to"
```

`voice.speaker` names the person at the machine and joins the two. **Unset, it stays
`"voice"` and they stay separate** — inferring which Discord user is sitting there would
attribute speech to the wrong person in a shared channel, which is worse than not joining
them. `/palintel status` reports which of the two is in force, because unattributed voice
is otherwise invisible until a follow-up mysteriously fails.

**What is genuinely unbuilt: telling two people in the same room apart.** That needs
speaker diarisation from one mixed stream — a different problem from the one
`SpeakerStream` solved, where Discord tagged every packet and the split was free. It wants
a speaker-embedding model and an enrolment step, and **no evidence has been gathered that
the case arises here**: it requires two people at one microphone, which is not the
setup any session so far has used. Building it now would be building for a hypothetical,
so it is recorded as a decision rather than an omission.

### Phase 2 exit — closed on its own bars, with A5 still open (2026-08-10)

**Phase 2 exit: intent ✅ · entity ✅ on the delivered classes · follow-ups ✅ ·
latency ◐ carried forward again.**

Scored on the full 232-utterance A5 set against Gemini 3.6 Flash, $1.34, 654s:

| criterion | outcome |
|---|---|
| ≥ 90% intent accuracy across both classes | ✅ **98.2%** (56/57) |
| ≥ 95% entity extraction | ✅ **96.5%** (55/57) on Q1 + Q2 |
| Follow-up resolution on a 20-case set | ✅ **22/22** |

| class | n | intent | entity |
|---|---|---|---|
| Q1 resource | 14 | **100%** | **100%** |
| Q2 pal | 43 | 97.7% | 95.3% |

**The wider enum cost nothing.** Q1 went from 4 resources to 19 and `find_pal_spawns`
gained a real description, and the headline moved the right way against Phase 1's final
measurement — 89.2% → **90.1%** exact, 3.4% → **3.0%** wrong, p95 6673ms → **6030ms**.
That is inside the ±1.5-point measurement band, so the honest claim is *unchanged, and
specifically not worse*, which is the thing that had to be established.

**Two denominators, and conflating them would flatter this.** 96.5% is entity extraction
on the two classes Phase 2 actually dispatches. Across all 232 utterances — seven query
classes, five of which have no implementation and exist in the harness only so an entity
has somewhere to go — it is **90.1%**. That is the A5 gate, it has been failing since
Phase 0, and it was accepted at measured behaviour there and again in Phase 1. Phase 2
does not change it and does not claim to.

**One genuinely wrong entity in 57**: *"where can I find Leithbunk"* → Lifmunk, where
Leezpunk was meant — the same hard case Phase 1 named when it rejected `thinkingLevel:
minimal`. One honest decline on a mangled name (*"Hippow where do Vidrus spawn?"*). Five
out-of-class prompts landed on `find_pal_spawns` with **the right entity every time**, and
at least two are defensible rather than wrong: the spawn card reports `encounter_share`,
which is a direct answer to *"how rare is Titrois?"*.

**A measurement bug found while scoring, and it was mine.** The class labeller folded
"how do I get X" into the location templates, so *"how do I get Broncherry Aqua"* was
scored as a location question and the model's correct `get_breeding_combo` counted as a
routing failure. Six of seven apparent intent failures were that. For a resource the
phrase means "where"; for a Pal it means breeding or catching. Fixing it also corrected
the fast-path Q2 denominator from 49 to 43 — every conclusion there held, since the
numbers that mattered were zeros.

**Latency, carried forward a second time.** The question Phase 1 posed was *what fraction
of a realistic two-class mix the fast path can still carry*, and the answer is **61%**
(37 of 61), down from 78% when Q1 was the only class — the drop it predicted, for the
reason it predicted. Router p95 is 6.0s on transcripts, but end-to-end voice p95 with STT
and rendering in the path still has no sample: that needs a play session, not a harness,
and it is the one Phase 1 exit criterion that has now been open across two phases.

**Known issue, not fixed here.** `crude_oil` is in the lexicon but not in the tool enum,
which is built from resources that *have* nodes — so the model cannot route it and cannot
reach the honest "crude oil isn't a mineable node, it comes from oil rigs" card; it
declines with something vaguer instead. The fast path claims it first in the shipped
stack, so the player sees the right card today, but that is the stub getting there first
rather than a design that holds. Recorded rather than fixed because changing the tool
schema immediately after measuring against it would invalidate the baseline above.

---

## Spike — card artwork: map crops and Pal icons (2026-08-10)

Two enhancement requests, spiked together on `spike/visual-cards`: mark location answers
on a world map, and show each Pal's picture on Pal queries. **Adopted 2026-08-10** —
[ADR-0017](adr/0017-card-artwork-from-game-assets.md) is Accepted and both are on by
default, the flags surviving as off switches because the assets are a separate build.

Accepted *at measured behaviour* rather than at a clean bill of health, the same posture
A5 was taken under. What the design is organised against is measured; `art_post` p95, and
whether a marker lands on the actual rock, are open and knowingly carried. See the ADR's
"Accepted at" section.

**Both are feasible from the pak, and the game supplies more than expected.**
`DT_WorldMapUIData` publishes the world-space rectangle each basemap covers, so the world
→ pixel mapping is the game's own rather than another regression — and it independently
corroborates the fitted `coord_transform.json`. Icons join the lexicon on `internal_ids`,
which already exists: **285 of 286 Paldeck entries** have one, the sole gap being Rayhound
Cryst, which has no icon asset at all.

**Two traps, both of which produce an authoritative-looking wrong picture.**

*There is more than one map.* MainMap and Tree are separate textures with disjoint
rectangles, and 1,269 extracted placements sit inside Tree and outside MainMap. Plotting
everything on the main island puts those markers in open sea. Region is chosen by bounds
and priority; a coordinate matching none, or a set straddling two, gets **no picture** —
not a clamp, and not the region holding the most, because a crop showing two of three
answers silently disagrees with the text above it.

*Bounds do not imply orientation.* Which world axis drives the image column, and whether
either runs backwards, is a separate fact — and it is **again an axis swap**, exactly as
in spike 0.5, plus an inverted row. It is measured rather than assumed: three independent
classifiers score all eight layouts using every extracted placement as known terrain, and
the ingest fails closed unless they agree.

| classifier | MainMap | Tree |
|---|---|---|
| land colour | 77.8% vs 46.9% | 58.0% vs 43.5% |
| not background | 95.9% vs 93.6% | 93.6% vs 79.9% |
| local detail | 69.8% vs 51.2% | 67.6% vs 57.9% |

**Unanimity is the gate, not a margin, and that was a correction.** The first version used
the colour classifier alone at a 15% margin — and it *rejected the Tree map*, whose
orientation an overlay showed to be correct. The classifier was reading that map's dark
forest floor as ocean. Loosening the threshold would have weakened the check everywhere to
accommodate one map; adding two classifiers that fail in different places was the fix. No
single one is strong on both maps, and choosing whichever looked decisive on the map in
front of me would have been fitting the measurement to its example.

**Illustrating inside the answer path cost the answer, and that was the second
correction.** Rendering during `handle()` moved p95 from 76 ms to 508 ms — a Pal with
spawn areas 1,000 map units apart needs a 3,570 px crop, 64 tile decodes and a 12.7
megapixel resize. Posting the text card first only deferred the *upload*, not the render.
Fixed properly: `Outcome` carries a deferred `illustrate`, the bot posts the answer and
then draws, and a second zoom level (a 1024² whole-region overview) bounds the wide case
instead of refusing it.

| | text only | artwork on |
|---|---|---|
| `handle()` p50 / p95 | 51.2 / 76.4 ms | 52.8 / 77.9 ms |
| deferred draw p50 / p95 / max | — | 7.8 / 25.5 / 25.9 ms |
| payload per card | — | p50 65 KB, max 87 KB |

Assets cost ~20 MB local and a two-step build per patch, gitignored like every other
game-derived artifact — this is game *art*, the clearest case for not redistributing.

### The spike's most valuable output was a data defect, not a picture

First real query on the flag — *"can I get coal at this level?"* — drew all three markers
in open ocean. The renderer was correct: both projections agree to the pixel, and the
player marker landed on land in the same crop.

**16.5% of the resource-node dataset is dungeon-local coordinates published as overworld
positions**, and it had shipped since Phase 1. The
text card said *"(224, -600) | 1 deposit | 114 units away"* — indistinguishable from a
real answer. Nothing in the pipeline could have caught it: the coordinates are
well-formed, inside map bounds, and correctly transformed from what the extractor found.
They are simply not places.

Fixed at the ingest (`is_overworld`), which lifted coal's on-terrain rate from **73.0% to
92.9%**, and guarded by `tests/test_node_scope.py` — two tests by deliberately different
mechanisms, one on the cell-name rule and one sampling published coordinates against the
basemap, so a new way of producing the same symptom still fails.

Two consequences worth stating plainly. **The Phase 1 placement-volume finding is amended
above** — its owner-chain fix operated entirely on dungeon actors. And **the answer set
got smaller**: 552 coal clusters became 308, because cave coal is most of Palworld's coal.
That is a real loss of coverage and the right trade — Q1 answers "where do I walk to", and
a cave interior coordinate cannot answer it. Locating dungeons *as dungeons* is a separate
feature with its own data model, not a filter to relax.

**This is the argument for the pictures that no latency number makes.** A map crop is the
only output this project has that can be checked at a glance against ground truth, and it
found in one query what two phases of text cards, a validation suite and a density guard
did not.

### Also shipped: "drops from" on resource cards

A resource has a second acquisition route the coordinates never mention, and it matters
most when the coordinates are unreachable — so `resource_card` now carries a capped line,
on the no-results card as well as the normal one:

```
No Coal found
Nothing matched in my data. Try without a level limit.

Also drops from: Blazamut (10), Blazamut Ryu (10), Pierdon (4-5)
```

`DT_PalDropItem_Common` inverted against the 18 locatable resources; **11 have a
dropper**. Three ingest judgements are published with the dataset (§3.8 of
[03-data-ingestion.md](03-data-ingestion.md)): rate-0 rows excluded (48 of them, and
naming a Pal that never yields the item is a fabricated value), boss variants credited to
their base species as a **stated inference** with an `alpha_only` marker, and quest/NPC
actors excluded.

Two things measurement settled that guesswork would not have. The boss-collapse inference
worried me most and was **load-bearing for nothing** at the time — every published dropper
also appeared on an ordinary row. That held only because the dataset covered the 18
locatable resources; widening it to all 151 droppable items for the query classes put
**35% of claims** on the inference. Amended where §3.8 describes it. And the drop
table's casing disagrees with the name table's (`Gorilla_ground` vs `Gorilla_Ground`),
which silently cost five real droppers until the join was made case-insensitive.

### Play session findings, and the two changes they forced

The session that graded latency also produced judgements a harness cannot, and two of them
changed shipped behaviour.

**The node data is now validated at a scale it never was.** 2-3 nodes checked per mineable
type, ~15-20 in total, **all accurate**. A4 was fitted on 11 boss landmarks and validated
on 7, and until now **no resource node had ever been stood on**. The one apparent miss was
the dungeon-filter bug, not the transform.

**Accepted as they are:** maps read as useful in play, and the lag was fine despite the
p95 failure - which is worth recording as a tension between the bar and the product rather
than resolving one against the other. The "Also drops from" and "Ranch:" lines earn their
space.

**Pal locations were hit or miss, and the ordering was why.** Asked for Cattiva from
(-342, -250), sorting by distance returned a **1-point** area 191 units away and never
mentioned the **60-point** one - technically nearest, and a place you can stand and see
nothing.

Raw spawn count is not the fix either. Two of Cattiva's biggest areas carry a **3%**
encounter share, so 27 spawners mostly roll something else - exactly what `encounter_share`
exists to warn about. Points times share is expected encounters, which is the question
being asked, so ordering is now **density, then distance**.

That also removed an inconsistency nobody had noticed: the no-position branch already
ranked by density, so "best" silently meant two different things depending on whether the
save could be read.

**`pal_drops` moved to the fast path**, measured offline against all 240 transcripts
because the stub is deterministic and the check therefore costs nothing:

| | |
|---|---|
| newly claimed | 8 |
| exactly right | **7** |
| taken from another branch | **0** |
| wrong entity | **0** |

The eighth is *"what do I get from Sigmyth and Pufflot"*, answered about Puffolt alone.
Sekhmet is absent from the candidate list entirely - a corrector recall failure - and
**the model declined that prompt in both registries**, so the fast path gives half an
answer where the model gave none, under a card titled "Puffolt drops" that does not claim
otherwise.

Two bugs found while building it, both invisible from the outside. The branch claimed
**nothing at all** until it moved above the location-cue gate: a drop question contains
none of `where|nearest|find|...` by construction, so the gate declined it first. And the
second-entity guard, which defers two-Pal questions to the model, had to become
family-aware - "Incineram Noct" ranks Incineram beside it at an identical score, and
treating that as two entities would have deferred every variant query for no reason.

**This does not fix the latency bar on its own, and the arithmetic says so.** p95 needs
under 5% of queries reaching the model. Drops were roughly 10-15% of a realistic mix, so
this moves the tail substantially - but `item_source` cannot be fast-pathed while items
stay out of the lexicon, and fast-path phrasing misses remain. Expect improvement, not a
pass.

### The latency criterion, finally measured — and it is a coverage problem

Carried forward through the Phase 1 and Phase 2 exits as "accepted at measured behaviour"
on samples too thin to grade. A 1h17m session with 87 answered queries cleared 30 of each
kind, so `/palintel status` graded it for the first time. **Both bars fail.**

| | p50 | p95 | budget | |
|---|---|---|---|---|
| Voice | 1.5s | **4.2s** | 2.5s | ❌ |
| Text | 0.3s | **2.0s** | 1.5s | ❌ |

**The medians are comfortable and the tails are a different population.** Stage p50s are
`stt 0.38s · route 0.09s · post 0.23s`, and a route median of 0.09s means the fast path is
claiming most queries. `/palintel recent` shows 11 of 12 at 0.0-0.1s and one model call —
*"what does Vanwyrm drop"* at 1.5s. Add `post` and that is 1.7s, against a text p95 of
2.0s; the same sum on voice (0.70 hangover + 0.38 stt + ~2.5 model + 0.23 post) lands at
3.8-4.2s against a measured 4.2s.

**So this is not a tuning problem, it is a coverage requirement, and the arithmetic is
unforgiving.** p95 is the 95th percentile, so the 2.5s bar is only reachable when **fewer
than 5% of queries reach the model.** The fast path claims resource and Pal *location*. It
does not claim `pal_drops` or `item_source`, both shipped today, and the play protocol's
text block is roughly 30% those. Any class without fast-path coverage puts p95 in the model
population by construction.

What follows:

- **`pal_drops` is fast-pathable.** *"what does X drop"* is as templated as *"where can I
  find X"* — a cue word plus a confident lexicon Pal match, the same shape the stub already
  handles for spawns. Precision must be measured over the A5 transcripts before it ships,
  exactly as adding `find_pal_spawns` to the stub was.
- **`item_source` is not.** Items are deliberately absent from the lexicon
  ([ADR-0016](adr/0016-entity-resolution-in-router.md) ranks what the corrector knows), so
  nothing ranks "flame organ" for the stub to match on. It stays on the model unless that
  decision is revisited — and it is the decision that keeps the item enum from polluting
  every other query.
- **Therefore the bar cannot be met while any shipped class lacks fast-path coverage.**
  That is worth stating plainly rather than deferring a fourth time: either every class
  gets a deterministic path, or the criterion is measuring something the design does not
  promise.

**Two numbers that were previously unmeasurable came free with it.**

`art_post` is **531ms p50, 1,157ms p95** over 70 attachments — the one figure
[ADR-0017](adr/0017-card-artwork-from-game-assets.md) was accepted without. The reflow
lands about half a second after the card, so the edit-in delivery holds and a single
message would have added that to every illustrated answer's graded latency. Render
measured 16/47ms against 7.8/25.5 locally, which is the same order with a busy event loop.

**Wake-word false positives: 1 in 53 activations** (one fired with no speech).
[ADR-0004](adr/0004-wake-word-activation.md) recorded this as genuinely unmeasured, since
four negative clips cannot support a rate. Decline rate was **10.3%** (10 of 97) against
19-21% across the eval runs, and declines cost 3.8s p50 — the routing policy making
declining the expensive judgement, visible in play.

### The shipping configuration, measured

Four classes in the consolidated tool - `resource_location`, `pal_location`, `pal_drops`,
`item_source` - which is what production runs. Decided against a rule written before the
run, so the numbers could not be read backwards into a verdict.

| | per-class + `pal_drops` | **unified, 4 classes** |
|---|---|---|
| exact | 89.7% (208) | **88.8%** (206) |
| **wrong entity** | 3.0% (7) | **3.9%** (9) |
| declined | 20.3% | 19.0% |
| latency median / p95 | 1,983 / 6,031 ms | 2,089 / 5,878 ms |
| cost / request | $0.0058 | **$0.0036** |

Paired: 5 losses, 3 gains, **McNemar exact p = 0.727** — indistinguishable. Zero transient
failures. Pre-registered rule: revert above 5% wrong, investigate more than 5 points off
baseline. **3.9% and 1.3 points: accepted.**

Cheapest configuration measured, at $0.0036 a request. The schema is 2,975 tokens and
cached; the cost is now 75% output tokens, which is thinking, and only 12% schema.

**A 60-prompt stratified pre-check at $0.22 preceded it**, and changed the recommendation
rather than rubber-stamping it. It found no gross break and no spurious item entities —
and surfaced that **the prompt set contains no item-source utterances at all.** Every one
of the 240 recordings predates the class, so no amount of running this eval measures
whether "who drops Flame Organ" works. Its three wrong answers were pre-existing acoustic
failures already in the batch-5 notes (*Astrum* → `Astegon`, *Cinnamom* → `Cinnamoth`).

**So one gap stays open and it cannot be closed by spending.** `item_source` is verified
only by hand. Validating it needs new recordings, which is a session with a microphone,
not an API call.

### `pal_drops` measured — free, and it found the case for consolidating

The first item-drop class, measured against the same 232 prompts as the consolidation
baseline. Same registry style, same model, one extra class.

| | 2 production classes | 3 (`find_pal_drops` added) |
|---|---|---|
| exact | 90.1% (209) | **89.7%** (208) |
| wrong entity | 3.4% (8) | **3.0%** (7) |
| declined | 19.8% | 20.3% |
| schema | 16,534 tok | 18,554 tok |
| cost / request | $0.0059 | $0.0058 |

**No detectable effect** — one prompt each way, against a noise floor of about three. The
third Pal-enum copy costs 2,020 tokens and nothing at all in money, because it is cached.

**The class works.** All nine drop prompts in the scoreable set routed to
`find_pal_drops`, 9/9 exact, with **zero leakage** to `find_pal_spawns`. That was the
specific risk: the two tools share a subject and differ only in what is asked about it —
*"where do I find Vanwyrm"* against *"what does Vanwyrm drop"* — so they are the closest
pair of descriptions in the registry. Per-tool discrimination held.

**And one prompt found a structural limit the earlier comparison missed.** P151, *"what do
I get from Astralym and Mycora"*, chose the right tool and returned only Astralym.
`find_pal_drops(pal)` has one slot; the question names two. This is not a routing failure
and no amount of prompt work fixes it — the schema cannot express the answer.

The consolidated tool can: its `pals` array takes as many as the question names. That is a
concrete argument for consolidation that the accuracy comparison could not surface, since
both registries were measured on their ability to fill slots that existed. **Two-entity
queries are a known weak class at 64%** (A5 final measurement), and one reason may be that
some of them have nowhere to go.

### Consolidation measured — accuracy-neutral, faster, and the cost argument was wrong

[01-architecture.md](01-architecture.md) §7 note 4 named a single `answer_query` tool as
the lever against enum duplication and said plainly that the accuracy trade was untested.
Measured, same day, same config, Gemini 3.6 Flash, 232 prompts, all seven query classes
registered both ways.

| | per-class (7 tools) | unified (1 tool) |
|---|---|---|
| exact | **90.1%** (209) | 88.8% (206) |
| **wrong entity** | **3.4%** (8) | **4.3%** (10) |
| declined | 19.8% | 19.0% |
| latency median | 2,299 ms | **1,819 ms** |
| latency p95 | 7,797 ms | **5,322 ms** |
| schema | 9,728 tok, cached | **2,014 tok**, uncached |
| cost / request | **$0.0059** | $0.0072 |

**Accuracy is indistinguishable.** Paired over the same 232 prompts: per-class wins 5,
unified wins 2, **exact McNemar p = 0.453**. Seven discordant prompts out of 232, against
a run-to-run noise floor of about three. All five per-class wins are unified *declining* —
Majex twice, Dinossom Lux, Whalaska Ignis, Silvance — so what consolidation costs, if
anything, is a little confidence on the hardest entities, not correctness. Wrong-entity
sits at 4.3%, under the pre-registered 5% revert condition.

**Latency is the real result: 21% off the median and 32% off p95.** Latency is this
project's binding constraint — the voice budget is 2.5s and unmet — so a third off p95
matters more than the accuracy question that motivated the experiment.

**The cost argument in note 4 does not survive contact with caching, and is corrected
there.** Consolidation is 4.8× smaller and *more* expensive per request, because the
unified schema is **2,014 tokens against Gemini's 2,048-token cache minimum** — it misses
cacheability by 34 tokens and therefore bills full rate on every request, while the bigger
per-class registry bills at 0.1× after the first. The 25×-cost figure assumed uncached
schemas throughout.

That inverts only while the cache is warm. Cached content has a 2-hour TTL and the eval
fires 236 requests back to back, which is the best case caching will ever see; a player
asking a question every few minutes between sessions is not that. Cold, the per-class
registry pays its full ~9.7k tokens against unified's ~2k.

**Two measurement errors, both mine, both worth recording.**

The first run scored 87.5% / 6.0% wrong and was **void**. Five of its six new errors were
*"should I use Cremis against the first tower"* returning `[Cremis, Zoe & Grizzbolt]`.
`evaluate_counter` takes `target` as a **verbatim string** precisely so the router never
has to resolve a tower to a species; folding every entity into the Pal-enum array made the
model's correct inference look like an invented entity. That measured the schema, not the
router — the same mistake `_router_tools.py`'s own docstring warns about, made one level
down. The corrected schema keeps `target` as free text.

The second is smaller: the first attempt reached for nullable enums before
`pal_spawn_schema`'s docstring reminded me they have no clean form under strict tool use.
Slots are 0..n arrays instead, which also lets `check_breeding_pair` name two Pals against
a single copy of the enum.

**Verdict: adopt the unified shape, but it buys nothing yet.** Production registers two
tools and ~1,418 tokens; consolidating those saves nothing today. The result that matters
is that adding the remaining five classes no longer costs an enum copy each, and does so
without a measurable accuracy penalty. Kept behind `unified=` with the per-class registry
intact so the comparison stays reproducible.

### Spike — ranch production data: roster yes, item no

Asked whether "ranched from" could join "drops from" on resource cards. **The roster is
extractable; the item each Pal produces is not.** Recorded in full so the next attempt
does not re-walk it.

**All 284 data tables under `Pal/Content/Pal/DataTable/` were enumerated**, not sampled —
the search was reasonably suspected of being "I just haven't found the table yet", so it
was made exhaustive. Ruled out:

| Candidate | What it actually is |
|---|---|
| `DT_PalDropItem`, `DT_PalDropItem_Common` | Both defeat drops, identical shape, 1,044 rows each |
| `DT_MapObjectFarmSkillFruitsLottery` | The skill-fruit tree, 5 rows |
| `DT_MapObjectItemProductDataTable` | **Base facilities**, 16 rows — Well→WaterBucket, StonePit→Stone, OilPump→CrudeOil. Not Pals. |
| `DT_MapObjectAssignData` | All 22 fields are "who can work here" — `MonsterFarm_0` gives suitability, rank and `WorkerMaxNum: 4`, no item |
| `DT_PalMonsterParameter` | All **90** fields listed: `WorkSuitability_MonsterFarm` is a **rank** (Lamball = 1), nothing names an output |
| `DT_PartnerSkill` | 50 rows of cooldowns and costs |
| `DT_PartnerSkillAppendText` | 36 effect types, none of them ranch production |
| `PalMapObjectMonsterFarmParameterComponent` | Only `ActionIntervalSeconds` (50–80s) |
| `BP_<Pal>` actor blueprints | Reference `"SpawnItem"`, no item id, no data-table ref |

**The in-game description does not carry it either**, which was the most promising
remaining idea. All 310 English Paldeck long descriptions were parsed: **zero** mention a
ranch, a farm, or being assigned to one. They are flavour text — *"Wild Flambelle
surprisingly never get sick"* — with no mechanical content at all. The Paldeck's **Drops**
row, which is the thing that looks like it should say so, is `DT_PalDropItem`: defeat
drops, which we already have.

**The roster is `BP_Action_SpawnItem_<Pal>` — 32 assets**, which is the set of ranchable
Pals. But every one of those CDOs carries presentation only (`ChargeMontage`,
`FunFacialEye`, occasionally `SpawnSocketName`). Checked across Lamball, Chikipi,
Mozzarina, Flambelle and Beegarde: no item id anywhere. The mapping lives in
`ExecuteUbergraph` blueprint bytecode, which property extraction does not reach.

**It cannot be derived from the defeat drops either — now measured against the wiki's 29
rows as ground truth, rather than argued from examples.**

| candidate rule | holds |
|---|---|
| ranch items ⊆ the Pal's defeat drops | **25/29** |
| ranch items == the Pal's `MaterialMonster` defeat drops | **8/29** |

The category rule fails in every direction at once. It returns the **wrong** item for Mau
(ranches Gold Coin, whose only `MaterialMonster` drop is Leather), Woolipop (Cotton Candy
vs High Quality Pal Oil), Sibelyx (High Quality Cloth vs Ice Organ) and Caprity (Red
Berries — a `FoodVegetable` — vs Horn). It returns **nothing** for Beegarde, Chikipi and
Mozzarina, whose Honey, Egg and Milk are not `MaterialMonster` at all and who have no
such drop. It returns **too much** for Cawgnito, which drops three and ranches one. And
the subset assumption itself breaks on Vixy, whose ranch output includes Pal Spheres that
are not defeat drops in any category.

That is the [ADR-0007](adr/0007-entity-lexicon-boundary.md) failure precisely: a rule that
reproduces the handful of cases used to write it and guesses on the rest. Measuring it
against all 29 is what turned "tempting" into "28%".

**Vixy is the case that settles the approach.** `CuteFox` is on the roster and digs a
*set* of items rather than producing one, so any mapping has to be one-to-many. A text
parse could never have produced that, and a "single product" schema would have been wrong
for it — which is an argument for curation over inference independent of where the data
turned out to live.

**Resolved 2026-08-10: sourced from the community wiki instead of curated by hand.** 29
rows from [palworld.wiki.gg/wiki/Ranch](https://palworld.wiki.gg/wiki/Ranch), 28 of them
corroborated against the pak roster, published with `provenance: community-wiki` and a
per-entry `roster_verified` flag. A scoped exception to
[ADR-0014](adr/0014-game-files-as-source.md), amended there.

**Backlog: find an authoritative in-game source.** The remaining route is reading
`ExecuteUbergraph` blueprint bytecode, which is a different order of work from property
extraction. Until that lands, this is the only dataset in the project whose facts are not
from the game files.

The curation option below is retained because it was the pre-wiki recommendation, and
because it is still the fallback if the wiki proves unreliable across a patch:

**Hand-curate 32 rows**, with direct precedent in
[03-data-ingestion.md](03-data-ingestion.md) §5 — `BaseSite.flatness_score` is
hand-curated on the same reasoning, that *"a fabricated score is worse than an honest
human judgment"*. 32 rows is bounded, each verifiable in-game in one ranch cycle, and the
**roster comes from the pak**, so a validation step can assert the curated table covers
exactly the extracted 32 and fail closed when a patch adds a ranchable Pal.

Worth noting for the item-drop work: Flambelle ranches **Flame Organ**, which is the
query that raised all this.

**Carried into use rather than resolved.** The Discord edit round trip is **not measured** —
every number above is local — so whether the reflow reads as helpful or as clutter is
still a real-play question. A second gap the pictures make visible rather than introduce:
`coord_transform.json` was fitted on MainMap landmarks only, so Tree-region coordinates go
through it unvalidated against the in-game Tree map.

---

## Short play run — the transform holds, and STT decides the routing path (2026-08-11)

The abbreviated session from `play-session-protocol.md` §Short run: block 6 spoken, the
drop fast path inspected in `/palintel recent`, and the four nearest ground-truth nodes
walked. The transform question the session was designed around closed cleanly; the
findings with consequences came from the routing side.

### The markers land on the rock

Ore, stone, wood and paldium at (227,-481), (224,-483), (237,-484) and (228,-490) were
walked and confirmed against the **regenerated** table — the post-fix dataset, not the one
that had 16.4% dungeon interiors in it. The coordinate chain works: pak → world
centimetres → `coord_transform.json` → map units → a rock a player can stand on. That had
never been demonstrated end to end.

**And not only at the nearest marker.** For each of the four resources the *further*
markers on the same card were walked as well, outside the base, with deposits standing
there. So this is not one lucky point: multiple markers, four independent resources, and
both inside and outside base extents. The clustering step is confirmed alongside the
transform, since a card's second and third markers come from separate clusters.

One thing worth recording so a later reader does not puzzle over it: the four *nearest*
nodes are inside one of the player's bases and the assigned Pals keep them mined out, so
those were verified by position rather than by a deposit standing there at the time. **A
fact about where this base happens to sit, not a product problem** — node state stays
unmodelled and the project does not code against it. The outside-base markers are what
carry the physical confirmation.

**And the far-field check was run too, which closes the question outright.** Quartz was
confirmed at **(-53,-960) and (-52,12)** — roughly 551 and 573 map units from the save
position, on different bearings and about 972 units apart from each other. Distance was
the one thing the near-field walk could not speak to: a fitted affine transform's error
grows with distance from its fit points, and the original fit used 7 MainMap landmarks
only. Two long-range points on separate bearings, plus a dense near-field cluster,
constrain rotation and scale as well as translation.

**So the transform is verified rather than assumed, for the first time since it was
fitted.** Five resources, near and far, separate clusters, deposits physically present.
Every coordinate this project prints for MainMap rests on a chain that has now been walked
end to end.

The Tree-region caveat is untouched by this and stays open — `coord_transform.json` was
fitted on MainMap landmarks, and nothing above stood on a Tree-region node.

### The drop fast path fires in speech, and STT decides which path a query takes

`pal_drops` was fast-pathed after the session that measured latency, so this is its first
sighting on real STT output. It works — and the three bands it sorts into are the finding:

| Transcript | Path | Reading |
|---|---|---|
| "what does Chillet drop" | **0.1s fast** | clean input, correct card, level bands intact |
| "what does man worm drop" | model, 1.5s | the pal floor correctly refused a token it could not rank |
| "Disneyland Ball Drop" (Lamball) | model, 3.0s | same, on a worse mangling |
| **"Vanworm", "Makora"** | **0.1s fast** | **close enough to match, wrong enough to matter** |

**The middle band is safe and the near-miss band is not**, which is the opposite of the
intuition that worse input is more dangerous. Badly-mangled input fails the floor and
lands on the model, which is the expensive-but-careful path. Slightly-mangled input clears
the floor and is claimed by the path that preempts the model entirely.

The sharpest case, deferred rather than resolved — typed *"Astralym and Mycora"* went to
the model at 1.7s and spoken *"Astralym in Makora"* took the fast path at 0.1s, twice.
`routing.py:457` defers a two-Pal drop question only when the **second** Pal clears the
lexicon floor, so STT damage to that name removes the guard and the single-slot tool
answers a two-answer question. The mechanism is confirmed in the code; **whether those
runs produced one card or two was never checked**, and that observation is the whole
finding. Backlogged deliberately rather than fixed on a guess.

Chillet's card rendered `__Alpha only__` and `__Level 80+ only__` correctly on the fast
path, which was worth confirming: the fast path skips the model, not the dispatcher, so
the level-band split that once claimed 30-50 Ancient Relics survives the new route.

### `item_source` on the model, as designed — and STT is a latency input

All six item queries routed to the model at 1.4-2.0s. Correct: items are deliberately out
of the lexicon ([ADR-0016](adr/0016-entity-resolution-in-router.md)), so nothing ranks
"flame organ" for the stub to match.

**But the two slowest queries of the session were the two most mangled transcripts** —
`Apal, Woodtrap's Wool` at 3.7s and `PayPal Wooddrop Spones` at 4.3s, the latter sitting
on the measured 4.2s voice p95. That adds a term the coverage analysis did not have:
transcript quality feeds p95 both by denying the fast path *and*, apparently, by costing
more once the model has it. **STT accuracy is a latency lever, not only an accuracy one**,
which changes what the STT backlog item is worth.

### Two of twelve activations were the wake word and nothing else

`Hey pal.` twice, 1.4s and 1.5s, both routed to the model to decline an utterance
containing no question, both inside the graded latency population. Fixed the same day:
`activation.bare()` plus a gate in `bot.py` ahead of `_answer`, so these never start the
graded clock. A confident wake match still gets one line back, because a silent drop after
the player audibly spoke is [ADR-0004](adr/0004-wake-word-activation.md)'s worst failure
mode; a marginal one stays silent as party chatter.

The gate does **not** catch a *mangled* wake word with nothing behind it — bare "Apal"
scores 0.60 against the 0.62 threshold. Left alone: moving `MIN_SIMILARITY` trades against
the chatter measurement in `activation.py`'s docstring, and that is not a trade this gate
is entitled to make by itself.

---

## Q5 groundwork — the owned roster, and a smaller question (2026-08-11)

Q5's exit criterion is *"recommendations contain only owned Pals"*, and `saves.py` read
position and unlocks only. So the roster was a gate in front of the phase, in the same
shape A3 is in front of Q3.

**It opened, and the reason is worth generalising: the question was too big.** Phase 0.3
recorded per-Pal detail as living behind `RawData` decoders that are stale on 1.0.2, and
filed fixing them as "bounded, well-understood work". But Q5 does not need per-Pal
detail. It needs **which species you own**, which is one `NameProperty` per entry. Read
with `PALWORLD_TYPE_HINTS` and *no* custom decoders at all, `Level.sav` parses and the
field is right there in the undecoded blob: **192 distinct species across 554 of 555
entries**, no decoder repaired. The one skip is the player's own character, which carries
no `CharacterID`.

Two corrections to the record, both found by trying it:

- **At least five decoders are stale on build 24467282**, not two. `character`,
  `map_model`, `foliage_model_instance`, `work` and `base_camp` all fail with "EOF not
  reached", so dropping them one at a time never terminates. "Custom decoders disabled"
  means passing `{}` for the whole set while keeping the type hints - dropping the hints
  too makes the parser read a 1.95 GB ASCII string out of the middle of a struct.
- **`CharacterSaveParameterMap` now holds 555 entries**, against 547 at Phase 0.3.

**And one join hazard that would have been invisible.** The save writes `Sheepball`; the
pak writes `SheepBall`. A case-sensitive join drops that Pal with no error of any kind -
it is simply absent from the owned set, and a Q5 card quietly omits a Pal you own. The
set is lower-cased at the boundary and a test pins the pair.

**And the roster is not a list of Pals.** Captured humans share the map, so the set
contains ids like `Believer_Crossbow`. Q5 has to intersect with the Pal roster rather
than trust the ids, or it can offer a captured raider as a counter to a tower boss -
a card that would be well-formed, confident, and absurd.

**The parser reads the property rather than matching near it, and that was not
fastidiousness.** The obvious regex - take the next identifier after `CharacterID` -
returns the *type tag* `NameProperty`, for 554 of 555 entries. A uniform, plausible,
total failure that reads like success in every summary statistic you would think to
print. Same family as coal coordinates that are in-bounds and correctly transformed and
not places.

Getting here took six failed iterations, twice announced as a two-minute parse. Recorded
because the estimate was wrong in a specific way: each failure was a different wrong
assumption about a data shape, and none of them was visible without running it.

---

## The branch batch — the cues survive speech, the names do not (2026-08-11)

31 prompts recorded for `counters` and `item_source`, the two classes no transcript
covered. Every prompt in the 240-transcript set predates both, so scoring counters over
them claimed nothing and changed nothing - a perfect score that proved only the branch
does no harm. This is the first measurement of whether it *works*.

**Scored twice, against the written prompt and against the transcript**, because that
split separates two failures that are otherwise indistinguishable from outside.

| | hit | deferred to the model, as designed | miss |
|---|---|---|---|
| written | 16 | 15 | **0** |
| spoken | 9 | 14 | **8** |

### The result is not what it first looked like, and the first reading was published

**Corrected the same day.** This section first said six of the eight spoken misses were a
destroyed *name*. That is wrong, and it was wrong in the way this project keeps being
wrong: plausible, consistent with the transcripts, and not checked against what the
lexicon actually did with them.

The lexicon recovered almost all of those names. Ranking the transcripts directly:

| | heard as | lexicon result | router floor |
|---|---|---|---|
| B09 | "fan worm" | Vanwyrm **0.71**, ranked 1st | 0.85 |
| B15 | "jit dragon" | Jetragon **0.82**, ranked 1st | 0.85 |
| B29 | "landball" | Lamball **0.80**, ranked 1st | 0.85 |
| B30 | "my kora" | Mycora **0.83**, ranked 1st | 0.85 |

Four of five "unresolved names" were resolved correctly and **first**, then refused for
sitting 0.02-0.05 under the confidence floor. Only two misses were genuine speech
failures, and in both the *cue* died while the entity survived perfectly - *"counters"* →
"count is" with Bellanoir at 1.00, *"weak to"* → "Week 2" with Necromus at 0.95.

So the bottleneck is **the acceptance threshold, not transcription**, and the 68% headline
is a lower bound rather than the pipeline's accuracy - `stt.py` says so outright and this
run demonstrates it: *"do not read a raw transcript as the pipeline's accuracy."*

One more thing visible only at this level: on B12 the cue word *"defeat"* itself ranked as
**Felbat at 0.67**, above the real answer. Cue vocabulary and Pal names share a space.

### Lowering the floor is not free, and that is what argues for aliases

Swept against both sets at once, because a knob moved on one is a knob moved for the
wrong reason:

| floor | new-batch hits | wrong entity on the 240 |
|---|---|---|
| **0.85** (shipping) | 9 | **0** |
| 0.83 | 10 | 2 |
| 0.80 | 12 | 2 |
| 0.71 | 13 | 3 |

One extra hit costs two wrong entities immediately. This project trades declines for
correctness in that direction and never the reverse, so the floor stays.

**The conclusion is that the fix has to be surgical rather than global.** An alias raises
one true match to 1.0 without loosening the bar for everything else; the floor cannot do
that by construction. That is the case for harvesting the mangled forms this eval already
enumerates - `score_stt.py` ends by calling them "alias candidates" - rather than for
turning a knob.

### The second-entity guard defect, reproduced under measurement

*"What do I get from Astralym and Micora?"* routed to `find_pal_drops`. "Micora" failed
to rank, so the guard at `routing.py:457` never fired, and a two-answer question was
claimed into a single slot. Backlogged on 2026-08-11 as *"the mechanism is confirmed in
the code; whether those runs produced one card or two was never checked"* - it is now
measured rather than inferred, on a transcript, from a prompt written to provoke exactly
this.

### Two smaller things the run found

**`\bbeat\b` does not match "beats".** The branch missed *"what beats Vanwyrm"* - a
plainer counter question than several it did claim - and on *"where's the nearest Pal
that beats Frostallion"* it answered the location half and silently dropped the counter.
Found by the written pass, fixed, and re-checked against the 480 older cases at 0 claimed
and 0 changed, so widening the cue cost nothing.

**"Paldium fragment" spuriously matches Paladius.** The item/Pal collision
[ADR-0016](adr/0016-entity-resolution-in-router.md) accepted knowingly, appearing in the
wild for the first time.

### A note on the scorer, because it was wrong before the router was

The first run reported 17 misses of 31 and only one was real. Eleven were `item_source`
prompts, which the fast path cannot claim by design; three were counter phrasings
deliberately left to the model an hour earlier because they put the named Pal in the
attacker position; one was a two-Pal drops question that defers by the guard. Scoring
those as fast-path misses measured the wrong thing.

Prompts now carry `expect_path`, and *"deferred to the model"* is a distinct outcome from
both *hit* and *miss*. Worth recording as its own lesson: **an evaluation harness can be
confidently wrong in exactly the way the system it measures can**, and a 55% failure rate
that turns out to be 3% is the same class of error as a card that looks reasonable.

---

## Phase 3 — Boss counters: Q5 (was Q3 + Q5)

**Split 2026-08-11. Q3 breeding moved to [Phase 3B](#phase-3b--breeding-q3--unscheduled),
and the pairing that named this phase is dissolved.** The two were paired to put the
hardest Tier 1 class next to the cleanest Tier 2 one; that reasoning was about
*implementation* difficulty, and what actually separated them was neither — Q5 needed
code, Q3 needs a game state nobody involved has reached. See the split note below.

**Q5 boss counters (Tier 2)**
- Element matrix (hand-entered, unit tested) + boss dataset
- Deterministic scoring function; calibrate the formula against known-good matchups
- **Candidate-set validation** — discard any Pal the model introduces that is not in the
  computed set. This is the phase where the Tier 2 discipline is first exercised; build the
  validator before the LLM pass, not after.
- `CounterPlanCard` with recommendation treatment

**Exit:** Q5 recommendations contain only owned Pals, verified across the eval set; the
counter card legible on the second screen.

**State:** built end to end 2026-08-11 — element matrix, boss dataset, owned roster,
candidate set, Tier 2 guard, counter card, fast path with chained dispatch, model path —
and **unplayed**. Nothing has answered a counter question in real play, so the exit
criterion is met by construction and not by observation.

---

## Phase 3B — Breeding: Q3 — **unscheduled**

Deliberately not numbered `6`. A number is a claim about order, and this is not late in the
order — it is **outside** it. Everything here should be built the week the gate opens,
whenever that is, including before or during Phase 4. What follows the number is sequence;
what follows this phase is a dependency.

**Blocked on [ADR-0008](adr/0008-breeding-graph-derivation.md)'s verification gate, which
is not a code task.** `tools/ingest/build_breeding.py` ingests the ranks,
[`breeding-verification.md`](breeding-verification.md) is generated from them, and
`tools/eval/score_breeding.py` waits to consume the results. What is missing is eggs
hatched in a game where breeding is unlocked, on Steam buildid `24467282`.

**The escape hatch was tried and it failed, 2026-08-11.** The sheet notes that breeding
mechanics are global — no save, no bot, no Discord, so *any* player on the right build can
run it — and that was written as the thing which keeps this off the critical path. It was
true and insufficient. A second player was lined up and **did not have breeding unlocked
either**, which is the one precondition the sheet states in prose and never lists beside
the build id. Two people's game progress is a narrower funnel than "anyone on the right
build" suggests, and the sheet has been corrected to say so.

*Generalise it:* a dependency that can be satisfied by anyone is still a dependency on
someone, and this one is on a **playthrough**, not a person. Delegating it does not shrink
it; it only changes whose save has to be far enough along.

**CORRECTED 2026-08-12 by the Phase 4 tech ingest, and the correction is large.** The
paragraphs above assume this playthrough has not reached breeding. Checked against the
save rather than recalled, it has: the Breeding Farm's four stated requirements — level
19, `ForestBoss` defeated, no prerequisite, 2 ancient points — are **all satisfied**, and
the Egg Incubator is already unlocked. See [§Phase 3B: the breeding gate is not where this
file said it was](#phase-3b-the-breeding-gate-is-not-where-this-file-said-it-was) under
Phase 4 for the table and the two caveats.

So the block is not a dependency outside the repo at all. It is two clicks in the
technology menu plus the cake production the sheet needs — real in-game work, on this
save, by this player. *The lesson is the one this repo keeps relearning about itself: a
recorded blocker is a reading, and readings go stale. Nobody re-checked this one for a
day because the escape-hatch failure made it feel settled.*

**Q3 breeding (Tier 1)** — unchanged in content, all of it pending the gate:
- Breeding ingest per the Phase 0.4 outcome — **done**, conditional on the gate confirming it
- `BreedingModel` behind the protocol; `breeding_path` BFS from owned Pals
- Multi-step chain card — the hardest rendering problem in the project, since a 3-step chain
  must stay legible on a card. Consider capping displayed depth and summarizing beyond it.
- Handle unreachable targets and equal-length paths (prefer chains using more owned Pals)

**Entry:** the gate closes — `score_breeding.py` run against a filled sheet, ADR-0008 moved
from Provisional to Accepted, or to the `TableBasedBreedingModel` fallback it names. Note
the ADR requires **100% agreement** outside the exception table and refuses partial
agreement as a tunable, so one refuted Block 1 row is a decision, not a data point, and it
is the decision that makes this phase substantially larger.

**Exit:** correct chains for 20 hand-verified breeding targets; the chain card legible on
the second screen.

**What the split buys, and it is the reason for doing it:** Phase 3 read as blocked when
only half of it was, and the blocked half was blocked on something that was not coming.
A phase that cannot close because of a dependency outside the repo will quietly absorb the
phase it is bundled with — Q5 has been shipped and unplayed since 2026-08-11 while the
phase containing it reported as blocked.

---

## Phase 4 — Advisory and knowledge: Q6 + Q4 + Q7 (target: 3 weeks)

**Built end to end 2026-08-12 and entirely unplayed.** All three classes land, three
datasets are new, and the exit criteria below are met by construction rather than by
observation — the same state Q5 was in after Phase 3, and the same warning applies.

**Three further classes landed later the same day**, taking `PRODUCTION_CLASSES` to 13:
base rating, base criteria, and the named technology lookup. Two pieces of instrumentation
landed with them — a per-query spend ledger and the session analyser that finally reads
the capture files. All five are recorded in their own subsections below. **None of it has
been played either**, and every subsection says so.

**Q6 progression (Tier 2)** — **built.**
- Tech tree ingest; validate the prerequisite graph
- `suggest_next_unlock` — deterministic candidate set, advisory ranking against a goal
- Degrade cleanly if A6 failed (ask rather than read)

**Q4 base siting (Tier 2)** — **built, and not as specified.** See below.
- ~~Curate ~20 sites with rationale and attribution~~
- Retrieve deterministically; synthesize the *explanation* only

**Q7 general knowledge (Tier 3)** — **built without embeddings and without synthesis.**
- Corpus ingest: chunk, entity-tag, ~~embed~~
- Hybrid retrieval (~~similarity~~ lexical + entity boost)
- ~~Grounded synthesis~~ **verbatim quotation** with mandatory citation
- **Threshold calibration** — done at n=33 rather than the 50 asked for, and the result
  is a ceiling rather than a threshold. See below.
- ~~Router fallback: unmatched Palworld questions route here instead of declining~~ —
  **not built.** `general_knowledge` is a class the router may *choose*, not a catch-all
  the declines fall into. The system is not a chatbot and this phase did not make it one.

**Exit:** every Tier 3 card carries a source ✅; out-of-corpus questions decline rather
than improvise ✅ (11/11 at the shipping floor); no Tier 2 card contains a candidate
absent from its computed set ✅ by construction — `progression.validate` is the guard,
built before any model pass exactly as this phase required.

### What the data said, and what it changed

**The tech tree is not a tree.** 17 of 588 rows carry a prerequisite at all, all 17 are
links in six straight chains, every target exists and nothing cycles. So "validate the
prerequisite graph" passes, and passes because there is almost nothing to validate. **The
real gate is `LevelCap`**, which every row carries and which spans 1–80, so progression in
this game is a level curve with a points budget. `Tier` is 0 on all 588 rows and is not
published — a column that never varies is not a category. Q6 was reshaped around that
before it was written.

**Two currencies, and they do not mix.** 51 rows are `IsBossTechnology` and spend the
save's `bossTechnologyPoint`; the other 537 spend `TechnologyPoint`. A card that summed
them would tell a player they can afford something they cannot, so the currency travels
with every cost.

**Player level is still unreadable, and the unlocked set implies a floor.** A technology
cannot be researched below its `LevelCap`, so holding one at 57 means the player is at
least 57. That is a derived claim and is labelled as one on the card, and it is safe in
exactly one direction: it can hide something available and can never offer something that
is not. A stated level in the utterance overrides it — a reading beats an inference.

**A `LevelCap` needed no new amendment.** STATUS's 2026-08-11 decision that "level" means
the Pal's, *except where the game itself states a player gate*, already covers this: the
mount work bought the exception and Q6 spends it.

**26 technology names shipped as raw markup in the first build.** The name table stores
pointers — `<mapObjectName id=|BreedFarm|/>` — and some rows spell the tag
`mapObjectname`. A case-sensitive pattern read those as plain text, so the Large
Incubator's name was the literal tag. Well-formed, entirely wrong, and found only because
a card was read rather than a count checked. Third casing trap in this project after
`Boss_Anubis` and `SkillUnlock_Thunderdog_Ice`: **the pak's casing is not to be trusted on
any join, including a join to a tag name.**

### Phase 3B: the breeding gate is not where this file said it was

**Checked against the reference save on 2026-08-12, through the Q6 machinery rather than
by recollection, and the block is not what STATUS and this document describe.**

`BreedFarm` — the Breeding Farm — states four requirements and the save satisfies all
four:

| stated requirement | value | the save |
|---|---|---|
| `LevelCap` | 19 | player level floor is **57** |
| `RequireDefeatTowerBoss` | `ForestBoss` | `BOSS_BATTLE_NAME_ForestBoss` = **true** |
| `RequireTechnology` | none | — |
| `Cost` | 2 ancient points | **40** available |

`Special_HatchingPalEgg` (the Egg Incubator) is **already unlocked**. So breeding is not
blocked on a playthrough that has not reached it: it is two clicks in the technology menu
and 2 of 40 ancient technology points.

**What that does and does not change.** It reframes the block from "waiting on someone
else's save" — the escape hatch that was tried and failed on 2026-08-11 — to work inside
this playthrough. It does **not** make the ADR-0008 sheet runnable today: hatching an egg
also needs a Cake, which needs a Ranch, a Mill, wheat, eggs, milk and honey. That is real
in-game work and it is not a dependency on another person.

Two caveats stated rather than glossed: the four requirements above are what the *table*
states, and the in-game menu may enforce something the table does not carry (a quest gate,
for instance, of the kind `RequireResearchId` hints exists elsewhere); and the
`EPalBossType::ForestBoss` → `BOSS_BATTLE_NAME_ForestBoss` join is an **inference on a key
name**, strong at 5 of 5 flags matching a valid enum value and stated nowhere. Both are
declared in `tech.json`'s `tower_join_note`.

### Q4 was built differently from the design, deliberately

The design was twenty hand-curated sites carrying a `flatness_score` "hand-curated, 0–1",
prose rationale and a source attribution. **None of that was built, and the reason is that
each third of it conflicts with something this project has already decided.**

- The prose and the attribution are community-sourced content. ADR-0014's amendment
  confines that to the ranch dataset alone, which STATUS already lists as the project's
  weakest provenance and an open backlog item. Twenty more hand-written rationales would
  make the exception the pattern.
- A hand-scored flatness is an **uncalibrated judgement**, and this project already has
  one it has not paid off (`min_player_level` / `danger`, uncalibrated since Phase 1).
- Nobody involved can curate twenty verified Palworld base sites, so in practice they
  would have been *invented* — the one failure mode this project refuses.

**What was built instead is the half the game states.** `BP_PalGameSetting` carries
`BaseCampAreaRange: 3500` world units — 7.63 map units through the same fitted transform
every coordinate here uses — so "where should I put a base for ore and coal" is set
membership over coordinates the node dataset already publishes and the 2026-08-11 marker
walk already validated. Multi-resource coverage is the new capability: no other tool here
can express "one circle reaching two things".

Corroborated rather than trusted: applied to the reference save's three real base camps at
(229, −487), (73, −399) and (285, 625), the radius contains 3, 2 and 1 node clusters —
small handfuls, which is what a base looks like. Corroboration, not proof; nobody has
measured the circle in game, and that is a play-session item.

**What it gives up, and the card says so on every one of them:** nothing found in the pak
says whether ground is flat, underwater, or inside a no-build zone. A site is *where the
resources are*, never "you can build here". Left unsaid, that would be this project's
signature failure in a new place — an in-bounds, correctly transformed, entirely wrong
coordinate.

### Q7's licensing risk was not mitigated; it was absent

**A7 was the last open assumption, narrowed by ADR-0014 to exactly one thing: the Q7 prose
corpus, listed there as "licensed community prose" — the only dataset still expected to
come from outside the pak. It does not have to.**

Palworld ships 45 developer-written help entries explaining its own mechanics (Pal
Breeding Farm, Elements, Sanity, Item Rot, Pal Rank & Essence Condensers, Predator Pals),
310 Paldeck descriptions, 64 journal notes and several hundred item, structure and
technology descriptions. Extracted and cleaned, that is **3,103 chunks and 394k
characters** — squarely inside the "a few thousand chunks, exact search, no index
structure" the data model sized for. Nothing in it comes from a community source, so A7's
remaining risk does not need managing.

**What that costs, stated rather than glossed:** the game explains its mechanics and says
nothing about playing well. This corpus answers *"how does the breeding farm work"* and
cannot answer *"what is the best base layout"*, and the second is a real question. That is
a genuine narrowing against the Q7 described above, and the honest trade for a corpus with
no licence question and a citation on every line.

Two exclusions worth recording. **NPC dialogue is left out** — 832 entries, 179k
characters — because it is in-character speech and a retrieval index cannot tell an
opinion from a mechanic; a card citing a merchant's banter as how the game works is a
confidently wrong answer wearing a source attribution. Tutorial prompts are left out as
control bindings.

### The Q7 threshold is a ceiling, not a floor

Calibrated at n=33 (`tools/eval/score_corpus.py`) rather than the 50 asked for, and
written by the person who built the class — so it measures the corpus and the plumbing
more than the retrieval.

| floor | answered right | declined right | WRONG | missed |
|---|---|---|---|---|
| 0.30 | 18 | 8 | 7 | 0 |
| 0.50 | 18 | 10 | 2 | 3 |
| 0.62 | 17 | 11 | 1 | 4 |
| **0.80** | **17** | **11** | **0** | **5** |

0.80 costs nothing against 0.62 and removes the last wrong answer, which was *"do my pals
get tired"* quoting a Castaway's Journal entry instead of the Sanity help page. Chosen by
the rule every threshold here is chosen by: where wrong answers start, not where coverage
stops improving.

**The informative column is `missed`, and it is the measured case for embeddings.** All
five are in-corpus questions asked in the player's words rather than the game's — the game
has a *Death* entry and the player says "die", a *Pal Rank & Essence Condensers* entry and
the player says "raise". They score 0.34–0.70, **inside the band the out-of-corpus
questions occupy**, so no threshold separates a paraphrased question from an unanswerable
one on lexical matching. That is not a floor to tune, it is a ceiling on the method, and
it is the number an embedding index has to beat.

**Two bugs found while calibrating, and they were the same bug twice.** The score summed
IDF with a title multiplier and divided by the query total, so one matched title word
could exceed 1.0 — which put a Castaway's Journal entry at **1.00** for *"how do I make a
sandwich"*, a question with no answer in 3,103 chunks. Bounding the score fixed that and
revealed the second: query words absent from the corpus were being *filtered out* before
scoring, so the same question became the single word "make" and any chunk containing it
covered 100% of it. A term in no chunk is the strongest evidence there is that a question
is out of corpus, and it now weighs against the match instead of vanishing from it.

### What was measured about the routing

Every new branch was swept over the 271 A5 transcripts against the same configuration with
it switched off, which is the check `score_fast_path.py` alone cannot make — it builds its
own router.

| branch | claimed | stolen from another class |
|---|---|---|
| Q6 progression (`suggest_next_unlock`) | 3 | **0** |
| Q4 base siting (`suggest_base_sites`) | 0 | **0** |
| Q7 corpus lookup (`lookup_corpus`) | 1 | **0** |
| Base rating (`rate_base_site`) | 0 | **0** |
| Base criteria (`describe_base_criteria`) | 0 | **0** |
| Q6 named lookup (`find_technology`) | 0 | **0** |

The last three branches landed after the first three and the whole table was **re-swept on
the shipping configuration** rather than carried forward, so every row is one run of the
same router: `find_pal_spawns` 43, `find_resource_nodes` 14, `find_pal_drops` 12, plus the
four above. The three progression claims are *"what should I research next"*, *"what should
I do about technology points"* and *"is it worth worrying about my next research?"* — all
three name a recommendation, which is what the cue was narrowed to require.

**Zero claims is a weaker result for the four newest branches than it looks**, and it is
worth saying so: the A5 corpus was recorded before any of them existed, so nothing in it
asks about a base site or names a technology. The sweep proves they do not *steal*. It
proves nothing at all about whether they *fire*.

`score_fast_path.py` is unchanged across the whole phase — 14/18 Q1, 43/49 Q2, zero wrong
— and `score_branches.py` is unchanged at 16/16 written.

**The Q6 sweep changed the branch before it shipped.** With the topic cue alone it claimed
five, and two of the five were *"can you explain technology points?"* and *"what changes
with technology points?"* — requests for an explanation, answered with a shopping list. A
recommendation frame is now required alongside the topic, which is the "a question opener
is not an intent" lesson read the other way: the opener has to name what is *wanted*.

And then the two branches composed: *"can you explain technology points"* is the single
utterance the Q7 sweep claims, and it comes back with the game's own Ancient Technology
help page.

### Rating a place instead of searching for one (2026-08-12)

Q4 as shipped answers *"where should I put a base for ore and coal"* — it ranks candidates
against each other. The question that came back was the mirror of it: *"how good is my base
location"*, *"rate this base location"*. Same data, opposite direction, and a genuinely
different class rather than a re-phrasing of the first: one searches, the other judges a
coordinate the player already cares about.

**No invented score, and that is the whole design.** `min_player_level` has shipped
uncalibrated since Phase 1 and STATUS has listed it as a known defect ever since, so a
1-10 base score was never on the table. Instead each criterion is **pass / fail /
unknown** against a bar taken from the game, and the headline is a **count of criteria
met** — never a weighted total. A count claims four things were checked and three held. A
weighted score would claim flatness is worth 0.3 of a base site, which nobody has measured.

**Three reference sets, and picking the wrong one produces a confident, useless answer.**

| criterion | scored against | why that set |
|---|---|---|
| Flat ground, water | the **32 areas the game marks** `BP_BaseCampPopularArea_C` | measured, they hold a median of three deposits and a median roughness of 24 cm — the designers are marking flat ground near water |
| Resources, nothing named | **every node cluster on the map** | "better than half the places you could build" |
| Resources, one named | **clusters of that resource only** | the median site holds three deposits of anything; the median coal site holds one coal. Nine coal is unremarkable against the first distribution and the best there is against the second |

That last row is the resource-narrowed variant — *"is this a good spot for a quartz
base"* — and it is the **one place a named entity is a filter rather than a reason to
abstain**, which is worth flagging against ROUTING_POLICY's usual direction. The card still
lists *everything* in range whatever was asked for: a spot chosen for quartz that also sits
on 30 stone is information the player wants and did not think to ask for. Only the
criterion narrows. Several named resources fall back to the map-wide distribution, because
a "quartz and coal" reference set does not exist and inventing one by summing two
percentiles would be arithmetic on ranks.

**Two things the build got wrong first.**

- **The ranking put marked areas above deposits** and returned 2-coal sites over 9-coal
  ones. Flat ground is a *precondition*, not a ranking term, and the marked-area flag is a
  *tie-break*, not a reason. Order is now `(missing resources, flat, deposits, marked,
  distance)`.
- **The water bar contradicted itself on the card** — ❌ beside *"better than 78% of
  them"*. The bar was one base radius; the marked areas sit a **median of 23 units** from
  water, three times that. A bar stricter than the standard the designers build to fails
  spots they picked themselves. The bar is now theirs.

**Coordinates are accepted directly** — *"rate (185, -475)"* — and that path needs no save
at all, which makes it the only base class anyone can exercise without a running game.
**Off-map coordinates decline** rather than scoring 0 of 4: no resources, no water, no
marked area reads as a judgement about a bad spot instead of *"that is not on my map"*.

**And a third class came out of the same question.** *"What makes a good base"* is about no
place at all, and it could be answered two ways — *"these are the four things that make a
good base"*, which is a claim about how Palworld works and would publish somebody's opinion
as fact, or *"these are the things I check, here is each bar, here is where the bar came
from, and here is what I cannot check"*, which is a claim about this system's own method.
Only the second has a source behind every line, and it is what makes every rating card
interpretable. It names three gaps explicitly — buildability, raid safety, and anything
about how a base *plays* — because a list of four criteria reads as a complete account of
the problem and is not one. The in-game help guide has a *Base* entry and it explains the
Palbox; it says nothing about choosing where to put one, which is why the corpus lookup
declines this question rather than quoting it.

**The honest limit:** n=32 is a small yardstick. Percentiles move in steps of about three
points and only 15 of the 32 have a measurable roughness at all. It is a calibration taken
from the game rather than invented, and it is still not a large sample. Listed under
Known-uncalibrated in STATUS.

### The router picks a CLASS, and nothing had ever measured that (2026-08-12, $0.29)

`score_router.py` has always scored **entity resolution** — `expected` is a set of names.
Six of the then-twelve production classes name no entity at all, so on that axis
`base_rating`, `general_knowledge` and an honest decline are the same event. **The 88.8%
headline was a number about naming things**, and it had been read as a number about routing
for months.

| | |
|---|---|
| Correct entity, `--sample 60` | 89.7% exact, 3.4% wrong — matching the recorded 88.8% / 3.9%, which is what validated the harness |
| **Correct class** | **69.2%** (36 of 52) |
| Over-answered | 13 |
| Prompts now class-labelled | 930 of 1,031 |

**The first thing it found was about the harness, not the router.** Five of the thirteen
over-answers were the model choosing `compare_pals` and `get_breeding_combo` — classes
`score_router.py` registers and **the dispatcher does not have**. That is precisely the
mistake `unified_schema`'s own docstring warns about, committed in the file that warns
about it, and it is the fourth appearance of *"I searched for it is only as strong as the
term searched for"* wearing different clothes: offering fifteen classes measures the
registry, not the router. `--classes` now defaults to `production`.

**The remaining eight are real and they are one shape:** `pal_info` absorbing any question
that names a Pal and does not fit a narrower class — *"how much stamina does Rinjishi
have"*, *"is loopmoon worth levelling up"*. Whether a summary beats a decline there is a
judgement, now a measured one, and it is in front of the user as an open decision.

**A third of the eval corpus asks for something the product cannot do** — 344 prompts about
breeding combos, stamina and whether a Pal is worth levelling, labelled `unsupported`,
**where declining is correct and answering is the failure.** That is the opposite of every
other row and was invisible before the class axis existed. `unsupported` at 7% is the one
result that meets the written bar for spending the full ~$1.40 run, and it is a
decline-policy question rather than a routing bug.

**The decision rule is now written into `score_router.py`'s header** rather than left to
judgement after the fact: a wrong-class rate above ~10%, or any class under 50% on n≥10.
That ordering — rule first, run second — is the habit CLAUDE.md asks for and this is the
first run to follow it.

### What gameplay costs, logged per query (2026-08-12)

**Only the router costs money.** STT is local and free since ADR-0015, map crops and icons
are local CPU, every card is templated. So one ledger over one caller covers the whole bill.

The reason it exists is a specific past failure: **the roadmap records an eval that reported
a 13-point router regression which was in fact a depleted prepaid balance.** Every HTTP 429
arrived as a `Decline` and scored as an honest miss. A number on `/palintel status` is what
turns that from a mystery into a line item.

One row per query in `data/sessions/<session>/costs.jsonl`, beside the capture clips.
**Every query is logged, billed or not**, so the fast-path share falls out of the same file
rather than needing a second one — and `billed` is stored explicitly rather than inferred
from `usd == 0.0`, because an unpriced model also costs nothing and the two are different
facts. Evals write there too, under an `eval-<date>` session, since they are the dominant
spend and a balance that ignored them would be wrong in the direction that matters.

**No cached total.** Totals are computed by scanning the session files, which are a few
kilobytes each. This project has been bitten repeatedly by a recorded number going stale —
`main` being behind, the breeding gate, the roster — and a spend total is exactly the kind
of thing that would quietly drift from the rows it claims to summarise.

It never raises into the answer path, the rule `capture.py` and `saves.py` already follow.

**The balance is configured and currently 0**, so nothing is deducted and no warning can
fire. That is an open decision in STATUS, not an oversight: only the user knows what was
loaded onto the key.

### Reading the capture sessions — the rephrase label is not free (2026-08-12)

`capture.py` has written clips and a log since 2026-08-11 and **nothing had ever read
them.** The findings STATUS reports from that session were extracted by hand, and it
showed: of the ten manglings the session produced, **three reached the lexicon** — the one
failure run somebody worked through — and seven were still sitting in the log.

**The capture design called a rephrase "a free negative label"**: a failed query followed
within ~60s by a similar one that succeeds gives `(bad audio → correct entity)` at no
interaction cost. Measured against the only session that exists, it is not free.

| pair | frame similarity | verdict |
|---|---|---|
| "beat Exo" → "beat Axel" | 0.81 | real |
| "about Lening" → "about Leneen" | 0.88 | real |
| "Gilderoy…drop" → "Gidra…drop" | 0.72 | real |
| "against Majoran" → "against Bjorn" | **0.76** | **not a pair** |
| "about Lani" → "about Orserk" | 0.69 | not a pair |

**The worst false positive scores higher than the best true positive.** Frame similarity
cannot separate them, and neither can similarity to the resolved entity (real pairs
0.29–0.50, false ones up to 0.44). Nothing in the feature set does, at n=1 session. So the
analyser **proposes and does not decide** — the same posture `harvest_aliases.py` already
takes, for the same stated reason: which manglings deserve permanence is a judgement about
one speaker's voice.

**The human feedback is the anchor, not the inference.** Nine feedback rows exist in that
session — 6 `misheard`, 2 `wrong_entity`, 1 `wrong_class` — and they are ground truth
rather than the router's opinion of itself. A `misheard` row says *this transcript was
wrong* with no guessing at all, narrowing the candidate set from 41 utterances to 6 before a
single similarity is computed. That inverts the design's emphasis and is worth stating
plainly: **the free label is the button press, and the rephrase is the hypothesis it makes
checkable.**

The window is **90s, from the data rather than the design's ~60s** — the clearest rephrase
in the session is 64s apart, and a 60s window would have discarded it.

**Failure runs are counted once.** Several attempts at one hard name, none answered, is
worth *more* than a single miss — it is several pronunciations of one word — and must not
skew the corpus by being counted several times. A run emits `expected: null` unless a later
success resolves it; guessing the name from a run of failures is writing fiction.

**Two defects in `harvest_aliases.py` surfaced immediately.**

- **It auto-accepted `majoran → Bjorn`, which is wrong.** Its four checks validate the
  *surface form* — length, edit distance, collision against the lexicon — and say nothing
  about whether the target is the right entity. Gameplay candidates are now always held for
  review, whatever they score.
- **The `review` pile was computed and never printed.** Pre-existing, and invisible for as
  long as nothing populated it.

**One reading of the session log was mine and it was wrong.** I first reported zero human
feedback because I looked for a `kind` key; the rows carry `feedback`. Nine rows were there
the whole time. Worth recording next to the rest, because it is the same shape as the
`patchnotes` tag and the three actor prefixes: the search term was wrong and the empty
result read as an answer.

### Naming a technology — 588 names matched in exactly one place (2026-08-12)

The test plan shipped with this written down as a **known gap**: *"naming a technology does
not work — technology names are ordinary English and are deliberately out of the lexicon,
the same call `item_source` made for items."* That reasoning was sound about the lexicon
and wrong about the conclusion, and the difference is worth recording because it is a
general shape, not a one-off.

**The lexicon ranks globally.** Anything in it competes for every utterance, which is why
151 item names are kept out — and technologies are worse: 46 have single-word names and
twelve are ordinary English (`Mine`, `Ranch`, `Mill`, `Sword`, `Sign`). *"Where can I go
mining"* would rank against the `Mine` technology on every query forever.

**A branch-local matcher is not the lexicon.** It only ever sees the object of an unlock
verb, so `Mine` is compared against *"a mine"* and against nothing else. The same names
that are unusable globally are unambiguous in that one position. `item_source` reached the
same capability by a different route — a 151-value enum in the schema, paid for on every
request — and this costs nothing, because the name never enters the schema at all.

**The verb alone was too broad, and the sweep caught it.** The first frame keyed on
`unlock | research | get | build | make | craft`, and `get` made the branch claim *"where
do I get high quality pal oil"* — an item question answered with a technology card, the
well-formed-and-wrong failure this project refuses. The fix is a frame rather than a
verb: *"how do I …"* or *"what do I need …"*. **"Where" asks for a place; "how" asks what
it takes.** After it, the sweep over the 271 A5 transcripts claims zero.

**Ties decline.** `squash` deletes digits on purpose — ASR splits invented words and the
digits are noise — so all five tiers of `GrapplingGun`..`GrapplingGun5` collapse to one
string and score 1.00 together. Several cakes do the same. The second-best-within-0.02
guard sends those to the corpus rather than guessing a tier, which is the rule
ROUTING_POLICY states for entities applied for the same reason: **a card cannot ask which
one you meant.**

**The card lists every gate, not the first missing one.** `_blocker` collapses to the most
fundamental failure because a ranked list needs a single reason; *"what do I still need"*
is a different question, and naming only the first gate would send someone to beat a tower
without mentioning they are also nine levels short. Each gate is ✅ / ❌ / **❔** — lab
research and an unread save are *unknown*, never *unmet*.

Verified against the live save: *"how do I unlock the breeding farm"* → **You can research
this now**, ✅ level 19 · ✅ 2 ancient technology points · ✅ ForestBoss tower defeated.
*"How do I get the egg incubator"* → **You already have Egg Incubator**, green, no
shopping list.

### One thing that was built in Phase 3, tested, and never connected

**`owned_species` was absent from the bot's `PlayerState` until this phase.** So every
counter card in the 2026-08-11 play session said *"I haven't read your Pals"* while
`saves.owned_species` sat working and unreferenced — including the cards the player was
pressing feedback buttons on. Same shape as the counter fast path being dark for a day:
measured in isolation, never wired.

The roster is now polled on the save watcher's own slow cadence (five minutes; it is a
multi-megabyte parse against the player save's few kilobytes), it appears on
`/palintel status` as its own line, and the reference save reads **194 owned characters**.
That line exists because the failure was invisible from the channel: a card saying it had
not looked reads as a deliberate caveat rather than as a read that never happened.

---

## The second play session — the first one that could kill you (2026-08-12)

**52 utterances, 57 minutes, six human labels, the first play of Phase 4.** The session
that produced the largest single correction to what this project *believed about itself*,
and the first one where following a card had a consequence in the game.

Findings split cleanly in two, and the split is the most useful thing in this section.
Three defects came out of the logs alone — deterministic, replayable, no help needed. Two
came only from the player saying what happened afterwards, and **no amount of analysis
could have produced them**, which is what the feedback work below is for.

### The spend ledger was wrong by 3.8×, and both of its numbers were the wrong ones

`/palintel status` reported **$0.3344 over 56 queries, 55/56 reached the model**. The true
figures are **$0.0880 and 16/56**.

`FastPathRouter.__getattr__` forwarded `last_usage` to the model backend, which sets it on
a call and never clears it. So every fast-path answer *after the first model call* read the
previous call's usage, was flagged `billed: True`, and had that call's cost added again.
The first row in the ledger is `billed: False` — it is the only query that preceded any
model call — and after that the flag never goes false again.

Corroborated two ways, which is what makes the corrected number trustworthy rather than
merely smaller: counting rows whose usage tuple *changes* gives 16, and 21 rows logged
`path=model` minus the 5 that the stub actually answered gives 16.

**Both questions the module's own docstring says it exists to answer were wrong** — what a
session costs, and what fraction of play reaches the model at all — and wrong in the
direction that empties a prepaid balance early. This landed the day after `cost.balance_usd`
was added to STATUS as a decision waiting on the player. Setting it against a ledger
over-reporting by 3.8× would have produced a wall of "balance exhausted" warnings with two
thirds of the money still there.

*Fixed* by making `last_usage` a property that returns `None` unless this route went to the
model, and by clearing it at the top of each backend's `route()` so a raising request cannot
leave the previous one's cost to be re-billed. `None` is a meaningful value here —
`charge_from(None, …)` logs a `$0` row with `billed=False`, which is how the fraction gets
counted — so it can never be left to a stale read.

**And a second bug underneath it, of a kind worth naming separately.** The bot decided which
path answered by testing `"cue" in outcome.call.rationale` — a string sniff over prose no
branch is obliged to write. `_tech_named_call` says *"named technology 'Breed Farm' at
0.98"*, so all five technology lookups in this session were answered by the stub in
milliseconds and recorded as model calls, in **both** the capture log and the spend ledger.
The path is now derived from whether a call happened, which is the fact both readers wanted
in the first place. *A measurement taken from how a component describes itself is not a
measurement.*

### A spawn card sent the player somewhere they died, and the right answer was in the file

Asked *"where can I find Anubis"*, the card offered three level 68–72 areas about 2,000
units away. The player travelled and could not survive there. The level **55** field alpha —
the one they had already beaten — sits 831 units away.

It was in the dataset the whole time, and it was not a ranking problem:

```
alpha   (-133.8,  -93.7)  lvl 55-55   3 pts  share 1.00  density 3.00  d= 831
normal  (-1294.9, -602.6) lvl 68-72  17 pts  share 0.05  density 0.85  d=2000   <- shown
```

**Nearest of all 26 areas and densest of all 26.** It would have ranked first on the
existing sort. `find_pal_spawns` falls through `SPAWN_KINDS` to the *first kind with any
rows*, so 25 ordinary areas hid it before the sort could see it. Saying the word "alpha"
retrieved it correctly; saying "boss" did not; asking the plain question gave the lethal
answer.

*Fixed* with a `Field alpha:` row rather than by merging the kinds — the fall-through's own
reasoning still holds, since interleaving a level 12 field spawn with a level 55 alpha is
not one answer to one question. The row states the fact and reorders nothing.

**This also produced the first calibration datum `min_player_level` / `danger` has ever
had.** That rule has shipped uncalibrated since Phase 1, asking for ~20 nodes of known
difficulty read in game and receiving none. A level 68–72 area is lethal at this player's
level, and nothing on the card said so.

### Three markers in one place, and the player was standing in a fourth

Asked for Lovander, the card gave three areas 818–872 units out. They read as one
destination because density is spatially clustered, so the top three by density all sit
inside one habitat. **There was a Lovander area 8 units away** — 4 spawn points at 15%
share against 38 at 39% — and the ranking never mentioned it, because distance enters the
sort only as a tiebreak.

Nothing here overturns the Phase 2 finding that distance-first ranking was *wrong*
(Cattiva: a 1-point area 191 units out while a 60-point one went unmentioned). The card
gains a `Nearest:` row and both numbers, and the footer now says *"numbered by likelihood,
not by distance"* so the two rows read as answers to two questions rather than as a
contradiction.

**The bar on that row is a chosen number and is labelled as one.** It fires only when the
nearest area is at most *half* the distance of the best-ranked one. Without a bar it fires
on noise: Anubis's closest ordinary area is 1,962 units against 1,997 — 2% nearer, lower
share, a row that would change nothing. Lovander's is 8 against 818.

### The minus sign nobody can pronounce

Every spoken form of a negative coordinate was unparseable:

```
'rate the spot at 9999, negative 9999'  -> None     # verbatim from the session
'rate the spot at 185, negative 475'    -> None
'rate the spot at 185 minus 475'        -> None
'rate the spot at 185, -475'            -> (185.0, -475.0)   # written only
```

Whisper writes a spoken minus as the **word**, always. `_COORD_FORMS` accepted only the
glyph. Since Palworld's map is negative over most of the island — the comment on that regex
says so itself — this was the common case for the feature, on the only channel it is used
on.

Two consequences, and the second is worse than a missed parse. The failed parse **fell
through to the player's own position**, so the card came back titled *"Where you're
standing"* about somewhere they had not named. And the off-map refusal built the same week
could never fire, because refusing a coordinate requires reading one: *"rate the spot at
9999, negative 9999"* was answered as a rating of (284, 625).

*Fixed* by rewriting the spoken word to a sign before matching, so all three forms gain it
at once. **Not** by widening the parser to accept `321-500`: reading that as (321, −500)
would read *"level 30-40"* as (30, −40), in bounds and confidently wrong, which is the exact
trade the strictness exists for. An announced pair that will not parse now **defers** with
a restatement request instead of substituting a position. Swept: 0 of the 271 A5
transcripts are claimed by the new decline.

That decline also forced a small correction next door. The restatement card carried a
hardcoded *"I've forgotten what we were talking about"*, written when an expired referent
was its only cause. It now shows the decline's own reason and nothing else — otherwise the
second cause gets a confident explanation of the first.

### One stated filter silently dropped, in the branch whose neighbour warns about it

*"What tech should I research for my mining pals"* returned the unnarrowed list, led by
**Advanced Arrow**. `_TECH_GOAL_WORDS` has no mapping for "mining", so `goal=None` and the
filter vanished. *"What weapon should I research next"* correctly titles the card
"— Weapon".

The player pressed `wrong_class`, and the comment ten lines above `_TECH_GOAL_WORDS`
describes this exact failure — *"a filter the player stated, silently gone, on the fast
path"* — while guarding only the ancient-points pool. **Not fixed in this pass**, and
deliberately: the general rule it wants is *a narrowing we cannot map means defer*, which is
the same rule `_base_call`'s `weak` flag already implements one class over, and applying it
to Q6 needs a sweep of its own rather than riding along with four unrelated fixes. It is
written down here so it is not rediscovered.

### `item_source` answers, and the class is narrower than its card claims

*"Where can I find cakes"* returned **"Cake comes from — Lovander | 1 | 1%"**. True, and the
wrong answer: cake is crafted at a Cooking Pot, and the player was mid-breeding-unlock, for
which cake is the consumable. *"Where do I get a high quality pal oil"* returned 41 sources
led by Mammorest at 100%, 5–10, which is genuinely useful.

So the long-open *"does `item_source` work?"* question resolves as: **it routes correctly, it
is right about drops, and its card title asserts more than the dataset holds.** `by_item` is
a drop table; *"Cake comes from"* is a claim about provenance. Also unfixed here, and the
fix is a title and a footer rather than a dataset.

### What the buttons could not say, and the change that follows from it

Six labels were pressed. Two were `wrong_class` for things that were not a wrong class —
the dropped Q6 filter above, and a corpus answer that restated the question. The taxonomy
(`misheard` / `wrong_entity` / `wrong_class`) is a **router's** vocabulary and the player
reached for the nearest button in it.

And the two defects that mattered most in this session — the lethal spawn card and the
rating of an unnamed place — are both *"I acted on the card and the world disagreed"*.
Neither is knowable until you travel. Replaying the session against the save recovered the
spend bug, the coordinate parser and the dropped filter, all deterministic; it could not
have recovered *"I walked to those coordinates and died"*, which arrived only because the
player said so.

So the feedback channel gained two things:

- **A fourth button, first in the row, that asks instead of diagnosing.** `📝 Not what I
  expected` opens a modal with one optional free-text field. The button rides in the card's
  existing `send()` payload at no extra API cost and the modal is created only on a click.
  The note is an **attachment to a label, never a replacement**: prose does not aggregate,
  and `harvest_aliases.py` and the scorers consume the label.
- **`/palintel wrong`, as a reply to the card.** A Discord reply already carries the message
  id, which is the join key `record_feedback` wants, and it works on a phone. `FeedbackView`
  has no timeout but plenty of scrollback above it; the message-id join was always
  retroactive and only the buttons were not.

Two smaller things fell out of building it. `capture` was local to `start_voice()`, so a
feedback channel reachable only from the voice path would have been dead for exactly the
case it exists for; it now lives at bot scope beside `spend`, for the same reason and after
the same mistake. And `read_session` silently drops feedback whose message no captured
utterance claims — fine while every card came from a clip, not fine once a reply can arrive
against a text-channel card — so `read_feedback` now returns every verdict joined or not,
and the analyser reports the orphans rather than losing the most expensive row in the file.

**The analyser prints the notes above every inference it makes.** Everything else in that
tool is a hypothesis formed by looking at transcripts; those are sentences the player wrote.
The session's own numbers argue for the ordering: both rephrase proposals it produced were
junk (`'grappling' → Anubis` at similarity 0.73 across 75 seconds, from two unrelated
questions), and its docstring already records that frame similarity provably cannot separate
real pairs from false ones. One sentence retires the whole inference.

### The three things the player said afterwards, and what each one was

The session's own logs were exhausted before any of these surfaced. Each arrived as a
sentence, and each was a defect.

**"It gave me three locations in the same place"** — the Lovander ranking above.

**"I asked while standing inside base 3 and got bases 1 and 2."** `MAX_CARDS` is 2 and the
order was the save's. Base 3 sits at (284.47, 625.09) and the player was at (284, 625):
**0.5 units inside it.** The two shown were 1,046 and 1,113 units away, and the card
finished with *"...and 1 more base not shown"*. Every number needed to choose correctly was
already on that card. Now sorted by distance when the position is known, and left in save
order when it is not — nothing to sort by is not a reason to invent an order.

**"There are nodes around the map you can place a crude oil extractor on."** This one is
the worst, because the product had published the opposite as a sentence:

> Crude oil isn't a mineable node - it comes from oil rigs, so there are no map locations
> to give you.

There are 185. `BP_LevelObject_OilField_C` is placed across the island (map x −1594..923,
y −1773..695), its CDO reads `ProvidableStaticItemId: { Key: "CrudeOil" }`, and the game's
own item text — in a table this project already ingests — says *"Obtained by installing a
Crude Oil Extractor in an oil field."*

The chain of reasoning that produced the false card is worth reading in order, because
every step was locally sound:

1. `PakExtract`'s `drops` mode reads blueprints whose filename starts
   `BP_PalMapObjectSpawner`. An oil field is a `BP_LevelObject`.
2. The derivation therefore produced no crude oil entry, so `_resources.py` recorded
   `UNPLACED_RESOURCES = {"crude_oil": "Crude Oil"}` with the comment *"it has no
   overworld spawner class - it comes from oil rigs"*.
3. `cards.NOT_PLACED` turned that into a sentence for the player, and cited
   `03-data-ingestion.md` while doing it.
4. `02-data-model.md` and this file recorded it as a correction *the data forced*.
5. Four tests asserted it, and the node dataset shipped it as a `known_gaps` entry.

**An absence in a filtered search became a claim about the world, and then propagated into
two documents, a dataset, a card and four tests.** This is the fourth time a filter written
for one purpose has been read as a census here — after 81 of 532 data tables, one key in
one boss table, and three actor prefixes of 1,295 classes — and the first time the
conclusion was published as prose to the player rather than left in a dataset.

*Fixed by widening the search, not by naming the class.* The `drops` mode now asks all 30
`BP_LevelObject_*` blueprints whether their CDO carries `ProvidableStaticItemId`; exactly
one says yes, and **which one is now a fact about the pak rather than a name somebody
typed**. The cell scan collects the whole `BP_LevelObject` family for the same reason —
~800 extra rows that cost nothing, since `build_resource_nodes.py` selects by class
membership in the derived map. Crude Oil's `type_b` is already `MaterialOre`, so it derives
as locatable with no further help, which is the test that this is a widening rather than a
second special case.

**Two things it changed downstream, and both matter more than the lookup.** *"Where should
I build my base for crude oil"* — asked in this session and answered with a bare node card,
because `_base_call`'s `weak` guard correctly refused a resource with nothing to measure —
now works. And the card says what kind of place it is pointing at:

```
Crude Oil locations
_These are **oil fields** - places to install an extractor, not deposits to mine._
**1. (282, 631)** | 2 fields | 6 units away | danger: high | lvl 41+
```

That last part is carried as `provided_resources`, derived from the **absence of a master
row** — a mined node has a `material_type`, which is its tool category, and an item
provider has none — so a patch adding another provider gets the note without anyone editing
a list. A coordinate under "Crude Oil locations" with no such line reads as an instruction
to go and swing a pickaxe.

`NOT_PLACED` is kept and empty. The mechanism is right — some material will be craft-only,
and a bare "no results" about it is the wrong answer — and what its one entry proves is
that **a card must not turn the absence of a search result into a claim about the world.**

### What did not change

`score_fast_path.py` is unchanged across all of it — 14/18 Q1, 43/49 Q2, zero wrong — and
`score_branches.py` remains 16/16 written. 671 tests green. The session yielded **zero
aliases**: both rephrase proposals came back `NO SURFACE FORM`, so unlike 2026-08-11 there
was no mangling worth making permanent.

One thing the session lost, and it is worth fixing before the next: `activity.py` keeps
latency in a one-hour in-memory window and writes nothing. The voice p95 of **6.2s against
the 2.5s budget** exists only in a status line pasted into a chat log. Costs persist,
latency does not.

---

## Phase 5 — Hardening (ongoing)

- ~~**Fast-path intent matcher**~~ — **pulled forward into Phase 1**, because the latency
  component it targets turned out to be ~2s rather than the 300–600ms estimated here, which
  made it the difference between passing and failing a Phase 1 exit criterion rather than a
  hardening nicety. Shipped for Q1 only; it needs re-measuring per query class as tools are
  registered, since a keyword matcher's failure mode is claiming another class's queries.
- **Discord voice receive (DAVE)** — see below. Upstream-blocked, not abandoned.
- **"Find dungeons near me"** — see below. Reclaims what the overworld filter removed.
- Lexicon growth from observed STT failures — standing task, not a one-off
- Corpus coverage expansion against the checklist
- Patch refresh exercised against a real Palworld update
- Local STT evaluation — removes the last unavoidable network hop and the per-query STT cost
- Optional: local intent model and local embeddings, making the system fully offline apart
  from Discord

### Backlog — "find dungeons near me" — spiked 2026-08-10, viable with a split

**The link exists.** `BP_DungeonPortalMarker_*`'s class defaults carry
`SpawnAreaIds: [{"Key": "Grass001"}]`, and every dungeon table keys on that same id:
`DT_DungeonItemLotteryDataTable` (32 rows), `DT_DungeonEnemySpawnDataTable` (59),
`DT_DungeonRewardSpawnerLotteryDataTable` (162) and `DT_DungeonLevelDataTable` (15). So
marker position → spawn area → contents joins end to end. That was the unknown the spike
existed to settle.

**Positions are extractable.** The cell scan now collects dungeon actors alongside nodes
and spawners — the same walk, since the owner-chain and transform problem is identical —
and found **31 entrances, 30 of them on land**, spread map-wide (map_x −1356..286, map_y
−1467..653) rather than clustered.

**But the two entrance types are not the same feature.**

| type | n | carries | gives |
|---|---|---|---|
| `BP_DungeonPortalMarker_Grass1` | 13 | `SpawnAreaIds: [Grass001]` | position **and contents** |
| `BP_DungeonFixedEntrance_*` | 18 | `DungeonNameRowHandle` only | position and a **name**, no contents |

All **12** fixed-entrance classes that exist in the pak were found, so that set is
complete. Portal markers are not: 11 classes exist — Desert, Snow, Volcano, Sakura,
Skyland, Viking ×3, Yakushima — and **only Grass1 is placed in the scanned cells**. Those
biomes are separate content (Sakurajima, Feybreak, Yakushima), so the likely explanation
is level assets outside `PL_MainWorld5`, which is what the scan walks.

**So the feature splits cleanly, and the smaller half is the useful one now:**

- *"Where's the nearest dungeon"* — buildable today against all 31, and honest.
- *"What's in it"* — buildable for 13 of them. Thin, and worth saying so on the card
  rather than implying the other 18 have unknown contents when they have a different
  kind of contents entirely.

**Two corrections to earlier assumptions here, both mine.** I had assumed dungeon contents
were the 6,086 excluded `L15_X0_Y0` placements; they are not. Contents are **lottery
tables** rolled per spawn area, which is why 142 data layers never mapped onto 15 spawn
areas. And I briefly read the first five entrance rows as starter-region-only before
checking the extent — they are map-wide.

One risk checked and dismissed: `DT_DungeonSpawnAreaDataTable` uses ids like `Meadow01`
while the level table uses `Grass001`, which looked like two vocabularies that would join
silently and wrongly. All 15 level ids appear in the item and enemy tables; the `Meadow01`
family is unused extras.

**Two findings from play that shrink this feature, and both arrived after the spike.**

*Random dungeons are transient.* `BP_PalGameSetting` carries
`DungeonSpawnParameterDefault: { RespawnProbability: 67.0 }`, so a portal marker is a
place a dungeon *can* appear, roughly two times in three — not a dungeon. A card saying
"there is a dungeon at (x, y)" would be wrong about a third of the time, which is the
failure this project exists to avoid. The in-game countdown seen on entering is
consistent with a per-instance lifetime, but **no duration exists in any table, CDO or UI
string checked** — like the ranch mapping, it is presumably in bytecode. Inferred from
mechanism, not read from data.

*The permanent ones are already on the in-game map.* The 18 fixed entrances are the
"Sealed Realm of the …" boss arenas, and the player can already see them. So the half of
this feature that can be stated confidently is the half nobody needs, and the half that
would be new is the half that can only be described probabilistically.

**Kept in the backlog, and deliberately not built.** What is left is "here are 13 places a
dungeon appears about two thirds of the time", which is a much thinner product than the
spike suggested at its high point. It also does **not** answer the coal question: cave coal
sits inside dungeons, and knowing where a maybe-dungeon is does not recover it.

Worth revisiting only if the other biomes' portal markers are located — Desert, Snow,
Volcano, Sakura, Skyland, Viking, Yakushima all have marker classes and none is placed in
`PL_MainWorld5` — or if the lifetime is recovered, which would let a card say how long is
left rather than how likely it is to be there.

### Discord voice receive — "blocked on DAVE" for months, and DAVE was never the block

> **Resolved 2026-08-13.** Read the original entry below first: it is preserved
> unedited because the way it was wrong is the point. The resolution follows it.

**The single largest capability the project has lost, and it is the only one lost to
something outside the repo.** Discord's DAVE end-to-end encryption broke voice reception
in py-cord ([pycord#3139](https://github.com/Pycord-Development/pycord/issues/3139)): the
connection succeeds, a sink attaches, and no audio ever arrives — a failure
indistinguishable from a wake word that never fires. Voice pivoted to the local
microphone in Phase 1 ([ADR-0004](adr/0004-wake-word-activation.md),
[ADR-0012](adr/0012-dual-input-channels.md)).

**What it costs, stated plainly rather than absorbed:**

- **Party members cannot ask by voice at all.** They are served by the text path alone, so
  for them text is not the convenient second channel ADR-0012 describes — it is the only
  one, and typing mid-fight is exactly the friction this project exists to remove.
- Multi-speaker attribution is moot rather than solved. A local mic cannot tell two people
  apart, so the Phase 2 item reduced to naming the one person at the machine.
- The voice half of the system serves exactly one player, which is a much smaller product
  than the one the design describes.

**What is already in place for the day it works.** `voice.py`'s `SpeakerStream` keys a
full wake-word pipeline per speaker id and is deliberately kept as dead code — Discord
tags every packet with a user, so per-speaker attribution costs nothing there and would
have to be rebuilt from scratch if deleted. `mic.py` and `voice.py` feed the same
transport-agnostic `UtteranceBuffer`, and conversation memory is already keyed per user,
so restoring reception is closer to configuration than to a port.

**Routes worth evaluating, cheapest first:**

1. **Re-test the upstream issue.** It is a live bug in an actively maintained library and
   costs minutes to check. Nothing here should be built before this is re-run.
2. **Another library.** discord.py, and the newer voice-receive forks, may land DAVE
   support on a different schedule. The receiver is one module behind an interface that
   already has two implementations, so swapping it is bounded work.
3. **Implement DAVE.** Discord's protocol is published and libdave exists, but this is
   cryptographic transport code in the audio hot path — a different order of commitment
   from the other two, and only worth it if both fail and party voice is wanted badly.

**How it gets checked**: re-run route 1 at each patch refresh, alongside the "patch refresh
exercised against a real Palworld update" item above. Both are the same kind of task —
something outside the repo moved, and only re-running tells you.

---

**Resolved 2026-08-13 — and none of the three routes was the answer, because all three
shared a premise that was false.**

Route 1 was right about cost and wrong about what to re-check. The first measurement taken
against a live channel — instrumenting py-cord's own receive path rather than reading its
warning — showed **DAVE decrypting 99.8% of packets**. The cryptography had been working
the entire time. Route 3, "implement DAVE", would have been months of hot-path
cryptographic code replacing a layer that was already correct, and its cost is exactly why
it was ranked last. It was ranked last for the right reason and would still have been
wasted.

What was actually broken sat *above* the cryptography: py-cord 2.8 shipped a new
`voice/receive/` package against the old `sinks/core.py`, so `start_recording()` raised
`AttributeError` before a single packet was read — for **every** sink, including py-cord's
own `WaveSink`. Twelve more defects below that. Fixed in
[`PyDiscordDave`](../../PyDiscordDave/README.md), which does none of the cryptography;
`davey` already did that correctly.

**The measurements that changed a decision, which is what this file is for:**

| Measurement | Decision it reversed |
|---|---|
| DAVE decrypts 99.8% of packets | "Blocked on DAVE" — the premise of this entire entry |
| `push == writes + discard` closing exactly | Located a 5-gate delivery failure that four separate patches had failed to move |
| Opus round trip scores **0.957** against a **0.951** microphone baseline | Killed the theory that 48→16 kHz aliasing was costing wake-word recall. The naive decimation in `voice.py` was the right call, and its own comment said "revisit only if wake-word recall says otherwise" — recall said no |
| `concealed=59{'passthrough_unencrypted': 59}` | Proved a *fix shipped hours earlier* was splicing 1.2s of synthesised audio into live speech, reaching the transcript as "PayPal" for "Hey pal" |
| Wake score 0.95 → mistranscription; 0.20 → perfect | Killed the assumption that detector confidence is a proxy for audio quality |

**Two of the thirteen defects were this repo's**, and neither could have been found without
Discord audio, because both are consequences of a property the microphone does not have:
**Discord stops transmitting entirely when a speaker stops talking.** `UtteranceBuffer`
counted quiet frames, which assumes silence still produces frames — so an utterance never
closed until the speaker said something else, and two questions 30s apart arrived as one.
And `_tail` plus openWakeWord's rolling context spliced pre-silence audio onto the front of
the next "hey pal". `WakeWord.reset` existed for exactly that boundary and nothing called
it.

**What this cost, stated plainly:** the capability was surrendered for months on the
strength of a library's warning string. The warning was true — reception *was* broken — and
said nothing about why. **This is the fourth time in this project that a blockage was named
from a symptom rather than from a measurement**, and the first where the misattribution was
to another project entirely. The cheapest possible check, attaching a counter to py-cord's
own receive path, was never run.

**What remains** is in [`STATUS.md`](../STATUS.md) under the same backlog entry, and none
of it is transport: wake-word recall still has no number (and the one apparent improvement
is confounded by a microphone change mid-session), proper-noun mistranscriptions belong to
the lexicon-alias item, and `artwork.py:52` is unrelated. **`mic.py` stays the default and
the fallback** — [ADR-0012](adr/0012-dual-input-channels.md) is restored, not replaced.

**How it gets checked from here**: `discord_voice.stats()` and the per-minute health line.
After these fixes a silent failure no longer raises, so a counter moving is the only
evidence there is — which is precisely how the fabricated-audio defect above was caught,
and how it would otherwise have shipped indefinitely reading green.

---

## Sequencing rationale

| Decision | Why |
|---|---|
| Phase 0 before code | Seven assumptions, each cheap to test, each able to invalidate weeks of work |
| Q1 first | Simplest data model; exercises the whole pipeline without graph search or synthesis |
| Text input in Phase 1 | Nearly free, and it makes every later phase testable without a microphone |
| Conversation memory in Phase 2 | Needs ≥ 2 query classes before follow-ups mean anything |
| ~~Q3 + Q5 paired~~ | Hardest Tier 1 with cleanest Tier 2; establishes candidate-set discipline early. **Dissolved 2026-08-11** — the pairing sorted the two by implementation difficulty, and what actually separated them was a dependency outside the repo. Q5 shipped; Q3 is [Phase 3B](#phase-3b--breeding-q3--unscheduled). *The lesson for future pairings: phases should be bundled by what blocks them, not by how hard they are to write.* |
| Q7 last | Depends on corpus ingest and threshold calibration, and its router fallback should not mask routing bugs in earlier phases |
| Fast-path matcher last | An optimization. Correctness first; the p95 target is met without it. |
