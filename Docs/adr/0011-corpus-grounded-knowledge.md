# ADR-0011 — Corpus-grounded general knowledge; cite or decline

**Status:** Accepted
**Amends:** [ADR-0001](0001-drop-vector-search-premise.md)

## Context

Scope expanded to include general questions about the game — *"What does the Artisan trait
do?"*, *"How does the condenser work?"*. Unlike the other six classes, these have no schema
and no enumerable candidate set. The corpus is prose.

This is the first genuine retrieval problem in the project, and it requires revisiting
[ADR-0001](0001-drop-vector-search-premise.md), which dropped vector search entirely.

**That decision was correct for the reasons given, and those reasons do not apply here.**
ADR-0001 rejected embedding *structured* data — data with a schema that similarity search
would discard, producing fuzzy answers to questions that had exact ones. Wiki prose never
had a schema to discard. Same technique, opposite justification.

The remaining question was how tightly to constrain answers. The model likely knows a good
deal about Palworld already. But that knowledge is frozen at training time, and Palworld
patches change trait effects, breeding, and base mechanics. A confidently wrong answer
about a mechanic the player then builds around is expensive, and — unlike a wrong
coordinate — the player may never discover it was wrong.

## Decision

Tier 3 answers are **grounded in an ingested corpus, with citations, or declined.**

```
question → hybrid retrieval (vector similarity + entity-match boost)
         → any chunk above relevance threshold?
             yes → synthesize over retrieved chunks only, cite source
             no  → "That's not in my sources."
```

Model priors are **not** a fallback. If retrieval finds nothing, the bot says so.

Supporting decisions:

- **Local vector index.** A few thousand chunks; exact search over stored vectors is
  sub-millisecond. No ANN structure, no index build, no external service — consistent with
  [ADR-0003](0003-local-first-process.md).
- **Hybrid retrieval.** Similarity alone underperforms when a query names a specific entity.
  Chunks are tagged with canonical entities from the same lexicon that drives STT correction
  ([ADR-0007](0007-entity-lexicon-boundary.md)), and entity matches are boosted.
- **Chunks carry `patch_version`.** Staleness is visible rather than silent.
- **Every Tier 3 card shows its source.** Not a footnote — part of the template.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Corpus first, model priors as fallback** | Better day-one coverage, but reintroduces confidently-wrong answers on patch-sensitive details, marked only by a label the player will learn to ignore. Coverage gaps are visible and fixable; silent staleness is neither. |
| **Model knowledge only, no corpus** | Zero ingestion work, but no citations, no patch currency, and no way to correct a wrong answer short of changing models. Incompatible with the project's honesty posture. |
| **Full-text search instead of embeddings** | Simpler, but fails on paraphrase — *"how do I make my Pals work faster"* should find the Artisan and work-suitability sections without sharing vocabulary with them. |
| **Hosted vector service** | Contradicts [ADR-0003](0003-local-first-process.md) and adds a network hop and cost for a corpus that fits comfortably in memory. |

## Consequences

**Positive**
- Wrong answers are rare and correctable — fix the corpus, not the model
- Coverage gaps are *visible*, so they can be closed deliberately against a checklist
- Citations let the player verify anything that matters
- Patch currency is manageable: re-ingest the corpus, and `patch_version` shows what is stale
- Keeps the whole system local

**Negative**
- Corpus ingestion is real work with real licensing constraints
  ([03-data-ingestion.md](../03-data-ingestion.md) §4, §7)
- Day-one coverage will have holes, and the bot will decline questions it could plausibly
  have answered. Accepted deliberately.
- Threshold calibration is delicate — too low invents, too high declines answerable
  questions. Calibrated in Phase 4 against a 50-question eval split between in-corpus and
  out-of-corpus.

**Neutral**
- Vector search returns to the design, at roughly 1/7th of the original scope and for the
  opposite reason. Worth stating plainly: ADR-0001 is amended, not reversed.
