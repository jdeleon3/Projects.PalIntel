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
| 0.1 | Post a rich embed; read it in the overlay **while playing** | A1 | Illegible → reduce card density; other viewing surfaces still work. **Not** a project kill. |
| 0.2 | Capture per-speaker PCM via Pycord; write a WAV | — | Unusable → evaluate alternative Discord libraries |
| 0.3 | Locate the save directory; parse owned Pals, bases, **and unlocked tech** | A2, A6 | Unparseable → Q3/Q5/Q6 degrade to stateless; revisit [ADR-0005](adr/0005-save-file-player-state.md) |
| 0.4 | Derive the combination table from ranks; check ≥ 100 known combos | A3 | < 100% agreement outside exceptions → scrape explicit combos |
| 0.5 | Extract node placements via FModel; fit the world → map transform; verify ~20 nodes in-game | A4 | Transform underivable → Q1 not viable as specified |
| 0.6 | Record 20 utterances with hard Pal names; measure STT raw, with keyterm boosting, with fuzzy correction | A5 | < 95% after both defenses → redesign entity handling first |
| 0.7 | Survey sources for **licence terms** and structural quality | A7 | No licensable source → Q7 corpus must be hand-written; scope Tier 3 down |

**Exit criteria:** A4 confirmed (v1 depends on it). A1, A2, A3, A5, A6, A7 either confirmed
or their fallback chosen and recorded as an ADR amendment.

**Progress: A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ · A7 ◐ · A1 ⬜ · A5 ❌ (86.1% vs 95%, 0% wrong)**

Remaining before Phase 1 can start:

| Spike | Blocker |
|---|---|
| **0.6 — STT accuracy (A5)** | Model and latency **resolved**; architecture **corrected** ([ADR-0016](adr/0016-entity-resolution-in-router.md)). Verdict measured in Phase 1: **86.1%, misses 95% gate, 0% wrong entities**. |
| 0.1 — overlay legibility (A1) | Needs a play session. Not a kill criterion; informs card density. |
| 0.4 — breeding combos (A3) | Confirmed via `CombiRank` + `DT_PalCombiUnique`. Gates Phase 3, not Phase 1. |

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
- `ResourceNodeCard` template tuned for overlay legibility
- Discord publisher with backoff

**Exit criteria**
- Voice p95 ≤ 2.5s and text p95 ≤ 1.5s over ≥ 30 real queries each
- Zero fabricated coordinates across the eval set
- Every failure mode in [01-architecture.md](01-architecture.md) §8 produces its card
- **Used during a real play session without disrupting it** — the only test that matters

---

## Phase 2 — Breadth and conversation: Q2 (target: 2 weeks)

- Full Paldeck + spawn ingest; complete lexicon generation
- All resource types for Q1
- `find_pal_spawns` + card
- **Multi-tool routing** — the first point requiring disambiguation between classes. Grow
  the eval set to ≥ 50 utterances spanning both.
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

- **Fast-path intent matcher** — deterministic matching for common phrasings, bypassing the
  LLM. Targets the largest remaining latency component (300–600ms).
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
