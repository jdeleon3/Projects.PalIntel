# ADR-0002 — LLM routes; deterministic code answers

**Status:** Accepted, **amended** by [ADR-0010](0010-three-tier-answer-model.md)

> **Amendment (ADR-0010):** The original binary — deterministic facts, plus one advisory
> class — did not accommodate the expanded scope. It is replaced by three tiers, which
> differ in how much the model may contribute: routing only (Tier 1), ordering and
> explaining a computed candidate set (Tier 2), or grounded synthesis over retrieved prose
> (Tier 3).
>
> The core invariant below is **unchanged**: coordinates, stats, and breeding pairs never
> originate from a model. Tiers describe what surrounds those values.

## Context

A voice assistant must absorb enormous phrasing variance — *"where's coal"*, *"I need
coal"*, *"find me a coal spot that won't get me killed"* all express one query. Language
models handle this well.

Language models also fabricate. In this system the values the player acts on are
coordinates. **A hallucinated coordinate sends the player somewhere that does not exist,
mid-game, possibly into a death.** That is the worst failure mode available here, and it
is a failure the player cannot detect until they have already travelled.

The tension: the LLM is the best available tool for one job (understanding) and
disqualified for another (stating facts).

## Decision

Split them cleanly.

The LLM's **sole** responsibility is converting an utterance into a typed function call:
selecting a tool and extracting parameters, with entity-valued parameters constrained to
enums generated from the lexicon.

Every factual value in the output card originates from a typed query result and reaches
the card renderer without passing through a generative model.

```
utterance → [LLM] → ToolCall(name, typed args) → [deterministic] → typed result → [template] → card
                                                                    ▲
                                                        no model touches this path
```

The single exception is Q4 (base siting), which is inherently advisory. Even there the
model may only reorder and explain candidates retrieved deterministically — it may not
originate a coordinate or a site outside the retrieved set, and its cards are visually
marked as advice.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **LLM generates the full answer from retrieved context** (standard RAG) | Coordinates pass through the model. Mitigations are probabilistic; the requirement is absolute. |
| **No LLM; grammar/keyword intent matching only** | Brittle against phrasing variance, which is exactly what voice input maximizes. Retained as a Phase 5 *fast path* for common phrasings with LLM fallback — the right role for it. |
| **LLM validates its own output against source data** | Adds latency and a second failure point to solve a problem better solved by removing the model from the path. |

## Consequences

**Positive**
- Coordinate fabrication is structurally impossible, not merely unlikely
- The execution layer is pure, typed, and fully unit-testable without model calls
- Model choice becomes a swappable detail; a smaller/cheaper/local model can be evaluated
  for routing without touching correctness
- Failures are legible: a wrong answer is a routing error, traceable to a specific
  misclassification, not an opaque generation artifact

**Negative**
- Answers are constrained to the registered tool contract. Questions outside it are
  declined rather than improvised — an accepted trade, since open-ended chat is an
  explicit non-goal.
- Adding a query class requires a new tool, implementation, and card template. Deliberate:
  it forces each class to be designed rather than accreted.

**Neutral**
- Intent routing becomes the dominant accuracy risk and the primary thing to evaluate.
  This is preferable to diffuse generation risk because it is measurable — hence the
  fixed eval set in [00-overview.md](../00-overview.md) §5.
