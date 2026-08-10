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
| 0.4 — breeding combos (A3) | Confirmed via `CombiRank` + `DT_PalCombiUnique`. Gates Phase 3, not Phase 1. |

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

**Phase 0 exit: A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ (gates Phase 3) · A7 ◐ · A1 ⊘ retired
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

1. **`crude_oil` is not a placed node.** No spawner class exists for it in the overworld.
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

**Still unpopulated:** `min_player_level` and `danger`. Both need wild Pal level data,
which comes from the `BP_PalSpawner_Sheets_*` actors already extracted.

---

## Phase 1 — Vertical slice: Q1 resource lookup (target: 2 weeks)

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

**Still open in Phase 1:** the latency and real-play exit criteria. Note what four sessions
have *not* produced: a single wrong answer. Every mangled noun either recovered correctly
or declined honestly.

---

## Phase 2 — Breadth and conversation: Q2 (target: 2 weeks)

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
- **Conversation memory** ([ADR-0013](adr/0013-conversation-memory.md)) — follow-ups now
  have something to refer back to
- Multi-speaker attribution in a shared channel

**Exit:** ≥ 90% intent accuracy across both classes; ≥ 95% entity extraction; follow-up
resolution correct on a 20-case eval set.

---

## Phase 3 — Graph search and scoring: Q3 + Q5 (target: 3 weeks)

Pairs the hardest Tier 1 class with the cleanest Tier 2 class.

**Q3 breeding (Tier 1)**
- Breeding ingest per the Phase 0.4 outcome
- `BreedingModel` behind the protocol; `breeding_path` BFS from owned Pals
- Multi-step chain card — the hardest rendering problem here, since a 3-step chain must stay
  legible in a small overlay. Consider capping displayed depth and summarizing beyond it.
- Handle unreachable targets and equal-length paths (prefer chains using more owned Pals)

**Q5 boss counters (Tier 2)**
- Element matrix (hand-entered, unit tested) + boss dataset
- Deterministic scoring function; calibrate the formula against known-good matchups
- **Candidate-set validation** — discard any Pal the model introduces that is not in the
  computed set. This is the phase where the Tier 2 discipline is first exercised; build the
  validator before the LLM pass, not after.
- `CounterPlanCard` with recommendation treatment

**Exit:** correct chains for 20 hand-verified breeding targets; Q5 recommendations contain
only owned Pals, verified across the eval set; both card types legible in the overlay.

---

## Phase 4 — Advisory and knowledge: Q6 + Q4 + Q7 (target: 3 weeks)

**Q6 progression (Tier 2)**
- Tech tree ingest; validate the prerequisite graph
- `suggest_next_unlock` — deterministic candidate set, advisory ranking against a goal
- Degrade cleanly if A6 failed (ask rather than read)

**Q4 base siting (Tier 2)**
- Curate ~20 sites with rationale and attribution
- Retrieve deterministically; synthesize the *explanation* only

**Q7 general knowledge (Tier 3)**
- Corpus ingest: chunk, entity-tag, embed
- Hybrid retrieval (similarity + entity boost)
- Grounded synthesis with mandatory citation
- **Threshold calibration** — the point where "not in my sources" fires. Too low invents;
  too high declines answerable questions. Calibrate against a 50-question eval set split
  between in-corpus and out-of-corpus questions.
- Router fallback: unmatched Palworld questions route here instead of declining. This is
  the change that makes the system a chatbot.

**Exit:** every Tier 3 card carries a source; out-of-corpus questions decline rather than
improvise; no Tier 2 card contains a candidate absent from its computed set.

---

## Phase 5 — Hardening (ongoing)

- ~~**Fast-path intent matcher**~~ — **pulled forward into Phase 1**, because the latency
  component it targets turned out to be ~2s rather than the 300–600ms estimated here, which
  made it the difference between passing and failing a Phase 1 exit criterion rather than a
  hardening nicety. Shipped for Q1 only; it needs re-measuring per query class as tools are
  registered, since a keyword matcher's failure mode is claiming another class's queries.
- Lexicon growth from observed STT failures — standing task, not a one-off
- Corpus coverage expansion against the checklist
- Patch refresh exercised against a real Palworld update
- Local STT evaluation — removes the last unavoidable network hop and the per-query STT cost
- Optional: local intent model and local embeddings, making the system fully offline apart
  from Discord

---

## Sequencing rationale

| Decision | Why |
|---|---|
| Phase 0 before code | Seven assumptions, each cheap to test, each able to invalidate weeks of work |
| Q1 first | Simplest data model; exercises the whole pipeline without graph search or synthesis |
| Text input in Phase 1 | Nearly free, and it makes every later phase testable without a microphone |
| Conversation memory in Phase 2 | Needs ≥ 2 query classes before follow-ups mean anything |
| Q3 + Q5 paired | Hardest Tier 1 with cleanest Tier 2; establishes candidate-set discipline early |
| Q7 last | Depends on corpus ingest and threshold calibration, and its router fallback should not mask routing bugs in earlier phases |
| Fast-path matcher last | An optimization. Correctness first; the p95 target is met without it. |
