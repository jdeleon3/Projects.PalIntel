# 00 — Overview, Scope, and Success Criteria

## 1. Problem statement

While playing Palworld, the player routinely needs information the game does not surface:
where a resource node is, where a Pal spawns, what breeding chain produces a target Pal,
which of their Pals counter a given boss, what to research next, and general questions
about how the game's systems work. Obtaining any of these today means alt-tabbing to a
wiki — which interrupts play, and in multiplayer leaves the character vulnerable.

Input is constrained by the game client: mouse and keyboard are captured during play, so
**voice is the only free input channel**. Output goes to a **Discord text channel**,
readable via the in-game overlay, a second monitor, a phone, or a Discord popout.

## 2. Core objective

A Palworld assistant, reachable by voice while playing and by text at any time, that
answers questions across seven query classes and returns a compact card to a Discord
channel — with **zero tolerance for fabricated factual values** (coordinates, stats,
breeding pairs) and explicit sourcing everywhere else.

## 3. The three-tier answer model

This is the central design artifact. Query classes differ not only in computation but in
**how much a language model is permitted to contribute** — and that distinction drives
every downstream decision. See [ADR-0010](adr/0010-three-tier-answer-model.md).

| Tier | LLM role | Card treatment |
|---|---|---|
| **1 — Fact** | Routing only; never touches a value | Authoritative |
| **2 — Computed advice** | Explains and orders a deterministically generated candidate set | Marked as recommendation |
| **3 — Open knowledge** | Synthesizes over a retrieved corpus | Marked as reference, with sources |

The invariant: **coordinates, stats, and breeding pairs never originate from a model.**
Tier 3 accepts synthesis risk in exchange for coverage, confined to prose questions where
approximate correctness is useful and error is cheap.

## 4. Query taxonomy

| # | Class | Example | Tier | Mechanism |
|---|---|---|---|---|
| Q1 | Resource location | *"Where's the nearest coal?"* | 1 | Typed query + distance sort |
| Q2 | Pal location | *"Where do I find Chillet?"* | 1 | Typed join (pal → spawn zone) |
| Q3 | Breeding path | *"How do I breed Anubis?"* | 1 | **Graph search** (BFS over combination graph) |
| Q4 | Base siting | *"Where should my 2nd base go?"* | 2 | Curated candidates + advisory ranking |
| Q5 | Boss counters | *"Which of my Pals counter Zoe & Grizzbolt?"* | 2 | **Scoring function** over elemental matrix × owned Pals |
| Q6 | Progression | *"What should I research next?"* | 2 | Candidate generation from tech tree + save state |
| Q7 | General knowledge | *"What does the Artisan trait do?"* | 3 | **Retrieval** over knowledge corpus + grounded synthesis |

Three classes deserve emphasis because they are commonly mistaken for retrieval problems:

- **Q3 is a traversal.** Finding a breeding chain is shortest-path from the Pals you own
  to a target. No similarity search computes a three-step chain.
- **Q5 is arithmetic.** Boss element(s) × effectiveness matrix × your Pals' elements,
  levels, and stats → ranked list. Deterministic given player state.
- **Q6 is set difference then ranking.** Tech unlockable at your level minus tech already
  taken, ordered by goal.

Only **Q7** is genuinely a retrieval problem, and it is the only class where the corpus is
unstructured prose.

## 5. Input channels

Voice and text converge at the lexicon corrector and share everything downstream.

```
voice → wake word → STT ──┐
                          ├─→ lexicon corrector → intent router → …
text  (channel message) ──┘
```

Text input costs almost nothing to support and makes the intent router testable without a
microphone, which accelerates every phase after v1. See
[ADR-0012](adr/0012-dual-input-channels.md).

## 6. Scope

### In scope (v1)

- Voice capture with wake-word activation; text input from the channel
- STT with Pal-name lexicon correction
- Intent classification and typed parameter extraction
- Q1 end to end — see [ADR-0009](adr/0009-v1-vertical-slice.md)
- Templated Discord cards
- Local save-file parsing for player state
- Short per-user conversation memory

### In scope (post-v1)

- Q2–Q7
- Multi-user support within a channel
- Fast-path intent matching to bypass the LLM for common phrasings

### Explicit non-goals

- **Any modification of game state.** Read-only with respect to the save.
- **Cloud hosting.** Runs on the player's machine. See [ADR-0003](adr/0003-local-first-process.md).
- **Server-side or anti-cheat-adjacent integration.** No process injection, memory reading,
  or packet interception. Local save files only.
- **Ungrounded answering.** The bot is conversational, but Q7 answers are corpus-grounded
  and cited. When retrieval finds nothing, it says so rather than answering from model
  priors. See [ADR-0011](adr/0011-corpus-grounded-knowledge.md).
- **General-purpose chat.** It converses *about Palworld*. Off-domain requests are declined.
- **Redistribution of scraped datasets.** See [03-data-ingestion.md](03-data-ingestion.md) §7.

> **Scope history:** "Not a chatbot; unclassifiable utterances are declined" was an earlier
> non-goal and has been **reversed**. The system answers open game questions via Q7. What
> remains non-negotiable is *grounding*, not *narrowness*.

## 7. Success criteria

| Criterion | Target |
|---|---|
| End-to-end latency, voice (end of speech → card) | p95 ≤ 2.5s |
| End-to-end latency, text | p95 ≤ 1.5s |
| Intent classification accuracy (eval set, ≥ 100 utterances across all classes) | ≥ 90% |
| Entity extraction accuracy after lexicon correction | ≥ 95% |
| Fabricated values in Tier 1 and Tier 2 cards | **0** (structurally prevented) |
| Tier 3 answers carrying a source citation | 100% |
| Idle cost | $0 |
| Marginal cost per query | < $0.005 |

The zero-fabrication criterion is an architectural invariant, not an aspiration: Tier 1
and Tier 2 factual values reach the card from typed results without passing through a
model. See [ADR-0002](adr/0002-llm-as-router.md).

## 8. Assumptions requiring validation

Each has a verification task in [04-roadmap.md](04-roadmap.md) Phase 0.

| # | Assumption | Risk if wrong |
|---|---|---|
| A1 | Discord cards are legible in the in-game overlay | Overlay viewing degraded; other surfaces (second monitor, phone, popout) unaffected |
| A2 | Palworld saves are parseable from local disk with community tooling | Q3, Q5, Q6 degrade to generic answers |
| A3 | Breeding is derivable from a per-Pal combination rank plus exceptions | Breeding graph needs thousands of scraped combos |
| A4 | Node coordinates are PAK-extractable and the world → map transform is derivable | Q1 answers wrong or unusable |
| A5 | STT with keyterm boosting reaches ≥ 95% on Palworld proper nouns | Entity extraction caps total system accuracy |
| A6 | The save exposes unlocked technologies | Q6 falls back to asking the player what they have |
| A7 | A licensable prose corpus of sufficient coverage can be assembled | Q7 coverage gaps; corpus grows incrementally |

**Status after Phase 0.3 / 0.5 / 0.7** — details in [04-roadmap.md](04-roadmap.md):

- **A6 confirmed.** The player save exposes `UnlockedRecipeTechnologyNames` (118 entries on
  the test save) plus tech-point balances. Q6 is unblocked.
- **A2 confirmed with a caveat.** Saves parse, but 1.0.2 uses Oodle (`PlM`) compression that
  the current `palworld-save-tools` release does not handle, and two `RawData` sub-decoders
  are stale. Both are bounded; per-Pal detail (Q3/Q5) depends on the decoder work.
- **A3 de-risked.** The breeding exception table is exposed as a distinct dataset, which
  corroborates the rank model.
- **A7 narrowed.** Licensing risk is now confined to the Q7 prose corpus, since structured
  data comes from game files ([ADR-0014](adr/0014-game-files-as-source.md)).
- **A4 confirmed.** The world → map transform is derived, independently validated (7
  held-out landmarks, worst error 3.0 map units against a 10-unit threshold) and
  **accepted** as [`data/coord_transform.json`](../data/coord_transform.json). The axes
  turn out to be **swapped** — exactly the failure mode that would otherwise have produced
  confidently wrong coordinates everywhere. **The hard gate on v1 is cleared.**

Save-format drift is now a **demonstrated** risk rather than a hypothetical one: the
compression codec changed between minor versions. This raises the value of the
`SaveParser` interface and the degradation path in
[ADR-0005](adr/0005-save-file-player-state.md).

**A5 remains the highest-rated accuracy risk, and measurement reshaped it.** STT does not
garble Palworld proper nouns — it renders them as confident English ("Helzephyr" →
*"health sphere"*). Fuzzy matching still ranks the correct entity first 79.5% of the time
and in the top 3 **89.7%** of the time; the original design simply discarded those
candidates at a threshold. Entity resolution has moved to the router, which has sentence
context and makes a forced choice
([ADR-0016](adr/0016-entity-resolution-in-router.md)). Router accuracy is now the binding
constraint and is measured in Phase 1.

**A1 is no longer existential.** The output is a Discord channel; the overlay is one
viewing surface among several. It informs card density, not project viability.
