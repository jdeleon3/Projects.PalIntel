# 01 — System Architecture

## 1. Architectural thesis

**A language model routes; deterministic code answers; retrieval grounds the rest.**

The LLM converts an unstructured utterance into a typed function call. Tier 1 and Tier 2
factual values originate from typed queries and reach the card without passing through a
generative model. Tier 3 permits synthesis, but only over retrieved corpus text, always
with citations, and never for coordinates or stats.

See [ADR-0002](adr/0002-llm-as-router.md) and
[ADR-0010](adr/0010-three-tier-answer-model.md).

## 2. System diagram

```
┌──────────────────────────────────────────────────────────────────────┐
│  PLAYER'S MACHINE (single long-lived Python process)                 │
│                                                                      │
│  Discord voice ──► Voice Receiver ──► Activation Gate                │
│                     (Pycord sink)      (VAD + wake word)             │
│                                             │ utterance              │
│                                             ▼                        │
│                                      ┌─────────────┐   ┌──────────┐  │
│                                      │ STT Client  │──►│   STT    │  │
│                                      │ + keyterms  │◄──│ provider │  │
│                                      └─────────────┘   └──────────┘  │
│                                             │ transcript             │
│  Discord text ──────────────────────────────┤                        │
│  (channel msg)                              ▼                        │
│                                    ┌──────────────────┐              │
│                                    │ Lexicon Corrector│◄── lexicon   │
│                                    └──────────────────┘              │
│                                             │ normalized text        │
│                                             ▼                        │
│  ┌────────────────┐                ┌──────────────────┐  ┌────────┐  │
│  │ Conversation   │───────────────►│  Intent Router   │─►│  LLM   │  │
│  │ Memory (5 min) │◄───────────────│  (tool calling)  │◄─│provider│  │
│  └────────────────┘                └──────────────────┘  └────────┘  │
│                                             │ ToolCall(typed args)   │
│                        ┌────────────────────┼────────────────────┐   │
│                        ▼                    ▼                    ▼   │
│                 ┌────────────┐      ┌────────────┐      ┌──────────┐ │
│                 │  TIER 1    │      │  TIER 2    │      │  TIER 3  │ │
│                 │ typed      │      │ candidates │      │ retrieve │ │
│                 │ query /    │      │  + ranking │      │ + ground │ │
│                 │ BFS        │      │            │      │          │ │
│                 └────────────┘      └────────────┘      └──────────┘ │
│                        │                    │                    │   │
│         Knowledge Base ┴────────────────────┴────────┬───────────┘   │
│         (in memory)                                  │               │
│                        ┌──────────────┐         Knowledge Corpus     │
│                        │ Save Watcher │         (local vec index)    │
│                        └──────┬───────┘                              │
│                    Palworld save files                               │
│                                             ┌──────────────────┐     │
│                                             │  Card Renderer   │     │
│                                             │ (per tier + type)│     │
│                                             └────────┬─────────┘     │
└──────────────────────────────────────────────────────┼───────────────┘
                                                       ▼
                                          Discord #copilot-hud
                                  (overlay / 2nd monitor / phone / popout)
```

Everything inside the box is one process on the player's machine. The only network
dependencies are the STT provider, the LLM provider, and Discord. All data — structured
and corpus — is local, so retrieval contributes no network latency.

## 3. Components

### 3.1 Voice Receiver

Pycord voice sink over the channel's PCM stream. Maintains a persistent WebSocket + UDP
connection to the voice gateway — the constraint ruling out serverless compute
([ADR-0003](adr/0003-local-first-process.md)). Per-speaker streams are kept separate so
concurrent talkers do not merge and queries are attributable.

### 3.2 Activation Gate

VAD discards silence; a wake-word detector matches the configured phrase. Only audio
*following* a match is buffered and forwarded. Idle cost is zero and party chatter never
leaves the machine — a privacy property as much as a cost one.
See [ADR-0004](adr/0004-wake-word-activation.md).

Endpointing closes the buffer on trailing silence (default 700ms) or a hard cap (10s).

### 3.3 Text Intake

Channel messages matching a prefix or addressed to the bot enter the pipeline directly at
the corrector, skipping wake word and STT. Same routing, same tools, same cards.
See [ADR-0012](adr/0012-dual-input-channels.md).

### 3.4 STT Client

Streaming transcription with **keyterm boosting** seeded from the lexicon — the first of
two defenses against entity corruption.

### 3.5 Lexicon Corrector

Second defense. Ranks lexicon entities against transcript n-grams using phonetic and
edit-distance scoring, and emits the **top-K candidates with scores** — it does not
decide. `"health sphere"` → `[Helzephyr 0.72, Helzephyr Lux 0.68, …]`.

**It does not threshold or reject.** Measurement showed a threshold here discarding
candidates that were correctly ranked first: 61.5% accepted versus 89.7% present in the
top 3. The corrector has the least context of any component and is the wrong place to
judge confidence. See [ADR-0016](adr/0016-entity-resolution-in-router.md).

Matching normalises away whitespace and punctuation before comparison, because STT
renders one invented word as several English ones — *"Lee's bunk"* for `Leezpunk`,
*"my Korra"* for `Mycora`. Comparing across that split without normalising drops
similarity below any usable threshold.

### 3.6 Conversation Memory

Per-user, per-channel ring buffer of recent turns with a TTL (default 5 minutes, 4 turns).
Stores **resolved state** — the tool called, entities extracted, result summary — rather
than raw transcripts, so follow-up resolution operates on structured facts and the context
window stays small.

Enables *"what about the next tower?"* and *"which of those is closest?"* — the difference
between a chatbot and a command line. Cleared by TTL, by `/palintel reset`, or on a
detected topic change. See [ADR-0013](adr/0013-conversation-memory.md).

### 3.7 Intent Router

One LLM call with tool calling enabled, given the tool schemas (§4), any live
conversation context, and the corrector's ranked entity candidates. Entity parameters are
constrained to lexicon-generated enums.

**The router owns entity resolution**, not just intent. It is the only component with
sentence context — *"against the first tower"* implies a combat matchup, *"how do I breed
X"* constrains X to a breedable species — and it selects from a constrained enum, so it
makes a forced choice rather than a threshold judgement. It declines when genuinely
unsure; that decline is the system's guard against confident wrong entities
([ADR-0016](adr/0016-entity-resolution-in-router.md)).

This makes router accuracy the binding constraint on entity extraction. It is **not yet
measured** — it needs a live model and is Phase 1 work.

Outcomes:
- **Specific tool matched** → dispatch to Tier 1 or Tier 2.
- **No specific tool, but a Palworld question** → `answer_game_question` (Tier 3).
- **Off-domain or unintelligible** → decline card.

Tier 3 as the fallback is what makes the system a chatbot rather than a command
dispatcher, while keeping specific classes on deterministic paths.

Post-v1: a deterministic fast-path matcher handles common phrasings without an LLM round
trip, cutting 300–600ms.

### 3.8 Execution Layer

**Tier 1** — pure typed functions over the in-memory knowledge base. No I/O, no model
calls, fully unit-testable.

**Tier 2** — deterministic candidate generation (elemental scoring, tech-tree set
difference, curated site filtering), then an LLM pass that may **order and explain** the
candidates but cannot add to or alter them. Enforced by validating the model's output
against the candidate set and discarding anything unrecognized.

**Tier 3** — retrieval over the local knowledge corpus, then grounded synthesis with
citations. If no chunk clears the relevance threshold, the answer is *"not in my sources"*
rather than a guess. See [ADR-0011](adr/0011-corpus-grounded-knowledge.md).

### 3.9 Knowledge Base and Knowledge Corpus

**Knowledge Base** — structured game data loaded into memory at startup from versioned
local files. A few thousand rows; changes only on patch.

**Knowledge Corpus** — chunked prose (wiki, guides) with embeddings in a local vector
index. Serves Tier 3 only. Local file, no external service.
See [02-data-model.md](02-data-model.md).

### 3.10 Save Watcher

Watches the Palworld save directory and parses player state — owned Pals, base coordinates,
player level, unlocked technologies — into a `PlayerState` refreshed on change.
**Read-only.** See [ADR-0005](adr/0005-save-file-player-state.md).

This is what makes Q3, Q5, and Q6 actionable: breeding from Pals you own, counters from
your actual roster, tech suggestions from what you have not yet unlocked.

### 3.11 Card Renderer

One template per result type, with **visually distinct treatments per tier** so the reader
can tell fact from recommendation from reference at a glance. Tier 3 cards always carry
source attribution.

Constraints: small render area, read mid-combat. Target ≤ 5 fields, high contrast, key
values prominent.

## 4. Tool contract

```python
# ---- Tier 1: fact ----
@tool
def find_resource_nodes(
    resource: ResourceType,
    max_player_level: int | None = None,
    near: Coord | None = None,
    limit: int = 3,
) -> list[ResourceNode]: ...

@tool
def find_pal_spawns(
    pal: PalName,
    time_of_day: TimeOfDay | None = None,
) -> list[SpawnZone]: ...

@tool
def breeding_path(
    target: PalName,
    owned: list[PalName],
    max_depth: int = 4,
) -> BreedingPlan: ...

# ---- Tier 2: computed advice ----
@tool
def recommend_counters(
    target: BossName,
    owned: list[OwnedPal],
    limit: int = 3,
) -> CounterPlan:
    """Score owned Pals against a boss/tower by elemental matchup, level, and stats."""

@tool
def suggest_next_unlock(
    player_level: int,
    unlocked: list[TechId],
    goal: ProgressionGoal | None = None,
) -> list[TechRecommendation]:
    """Tech unlockable now and not yet taken, ranked against a stated goal."""

@tool
def recommend_base_site(
    slot: Literal[1, 2, 3],
    priorities: list[BasePriority],
) -> list[BaseSite]: ...

# ---- Tier 3: open knowledge ----
@tool
def answer_game_question(question: str) -> GroundedAnswer:
    """Fallback for general Palworld questions. Corpus-grounded; cites or declines."""
```

`near`, `owned`, `player_level`, and `unlocked` are injected by the dispatcher from
`PlayerState` — never extracted from the utterance. "Nearest" and "my Pals" resolve against
live save data.

## 5. Sequence: Q1 resource lookup (v1 slice)

```
Player: "Hey Pal, where's the nearest coal?"

 1. Voice Receiver      PCM, per-speaker
 2. Activation Gate     VAD pass → wake word matched → buffer to 700ms silence
 3. STT Client          → "where's the nearest coal"
 4. Lexicon Corrector   "coal" → ResourceType.COAL
 5. Conversation Memory no prior context needed
 6. Intent Router       → find_resource_nodes(resource=COAL)
 7. Dispatcher          inject near=base_1, max_player_level=<from PlayerState>
 8. Tier 1 execution    filter by resource + level, sort by distance, take 3
 9. Card Renderer       ResourceNodeCard (authoritative treatment)
10. Discord webhook     POST embed to #copilot-hud
```

## 6. Sequence: Q5 boss counters (Tier 2)

Illustrates the candidate-set constraint that defines Tier 2.

```
Player: "Which of my Pals should I use against Zoe and Grizzbolt?"

 1-5. (as above) → normalized transcript
 6.  Intent Router        → recommend_counters(target=ZOE_AND_GRIZZBOLT)
 7.  Dispatcher           inject owned=<PlayerState.owned_pals>
 8.  Deterministic scoring
       boss elements → effectiveness matrix → score each owned Pal
       by (elemental multiplier × level ratio × relevant stats)
       → ranked candidate set, computed, not generated
 9.  LLM pass             may reorder within the set and write rationale prose
                          MAY NOT introduce a Pal absent from the set
10.  Validation           any Pal not in the candidate set is discarded
11.  Card Renderer        CounterPlanCard (recommendation treatment)
```

Step 11 is the point: the model shapes the *explanation*, never the roster.

## 7. Latency budget

From the moment the player stops speaking (voice) or sends a message (text).

| Stage | Voice | Text |
|---|---|---|
| Utterance endpointing | 300–800ms | — |
| STT | ~300ms | — |
| Lexicon correction | < 5ms | < 5ms |
| Intent routing (LLM) | 300–600ms *(measured 3.8–4.1s — see note 4)* | same |
| Tier 1/2 execution | < 5ms | < 5ms |
| Tier 3 retrieval + synthesis | +400–900ms | +400–900ms |
| Card render | < 5ms | < 5ms |
| Discord POST | 200–500ms | 200–500ms |
| **Total (Tier 1/2)** | **~1.1–2.2s** | **~0.5–1.1s** |
| **Total (Tier 3)** | **~1.5–3.1s** | **~0.9–2.0s** |

Notes:

1. **Retrieval is not the bottleneck** for Tier 1/2 — under 0.5% of the budget. The
   original concept's focus on millisecond database latency optimized a rounding error.
2. **Tier 3 is legitimately slower** because it adds a synthesis call. Acceptable: general
   questions are rarely asked mid-combat. It is excluded from the 2.5s target, which
   applies to Tier 1/2.
3. **LLM stages dominate.** Removing generative card formatting
   ([ADR-0006](adr/0006-templated-cards.md)) removed 300–800ms and the hallucination risk
   at once. The fast-path matcher targets the remaining routing call.
4. **The routing estimate is wrong, and it is the whole budget.** Measured against Claude
   Opus 5 the routing call takes **3.8–4.1s median**, six to thirteen times the estimate,
   which alone exceeds the 2.5s end-to-end target. It is not a tuning problem:

   | Configuration | Median | Output |
   |---|---|---|
   | Opus 5, `effort: low`, adaptive thinking | 4087ms | 72 tok |
   | Opus 5, `effort: low`, thinking disabled | 3768ms | 82 tok |

   Disabling thinking buys ~320ms and costs the correctness guarantee that keeps it on
   (see `routing_anthropic.py`), and the model emitted no thinking content at `low`
   anyway. Roughly 4s is what a frontier model costs for one tool call. Prompt caching
   was added for the ~8k-token tool schemas — it cuts cost substantially but **not
   latency**, since time-to-first-token is dominated by generation, not prefill.

   This makes two items that were Phase 2 conveniences into requirements for the voice
   path: the **fast-path matcher** (a confident lexicon match plus a template phrasing
   skips the model entirely) and **routing on a small model**. Neither is measured yet —
   the Haiku 4.5 comparison is the open question, and the A5 accuracy run must complete
   before either is chosen on anything but latency.

## 8. Failure modes

| Failure | Behaviour |
|---|---|
| Wake word false positive | Fails routing → decline card |
| Low STT confidence | Card asks for a repeat; no query executed |
| Router cannot resolve an entity from candidates | Card names the unrecognized token explicitly |
| Intent ambiguous between tools | Decline; never guess between query classes |
| Tier 1/2 query returns zero rows | Explicit "no results" card |
| Tier 2 model output references unknown candidate | Discard the addition; render validated set |
| Tier 3 retrieval below threshold | "Not in my sources" — never fall back to model priors |
| Follow-up references expired context | Ask for restatement rather than guessing the referent |
| Save absent or unparseable | Degrade to stateless answers; card notes state unavailable |
| STT or LLM provider unreachable | Card reports the outage; process stays connected |
| Discord rate limited | Backoff with queue; drop stalest queued card |

The consistent principle: **degrade to an explicit, honest card.** Never render something
authoritative-looking when the underlying data is missing or uncertain.

## 9. Security and safety posture

- **Read-only** with respect to game files. No writes to the save directory, ever.
- **No process injection, memory reading, or packet interception.** Local save files only —
  nothing resembling cheat tooling.
- **Audio leaves the machine only after a wake-word match**, and only the matched utterance.
- **Conversation memory is in-process and TTL-bounded.** Never persisted to disk.
- **Credentials** in environment or a local secrets file, never committed. `.gitignore`
  covers save-path config, which contains a local filesystem path.
