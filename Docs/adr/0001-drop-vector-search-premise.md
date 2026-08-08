# ADR-0001 — Drop vector search and the RAG premise

**Status:** Accepted, **amended** by [ADR-0011](0011-corpus-grounded-knowledge.md)
**Supersedes:** `HighLevel.txt` §1, §2 (Primary Database), §3.2, §4

> **Amendment (ADR-0011):** Scope later expanded to include general game questions (Q7),
> which are genuinely unstructured prose. A **local** vector index over a wiki/guide corpus
> was reintroduced for that class only.
>
> This amends the scope of the decision, not its reasoning. ADR-0001 rejected embedding
> *structured* data — data whose schema similarity search would discard, yielding fuzzy
> answers to questions with exact ones. Wiki prose never had a schema to discard. Same
> technique, opposite justification.
>
> Still rejected: vector search for Q1–Q6, and any hosted vector service.

## Context

The project originated from a technology-first prompt: a news item announcing native
vector search in Amazon DynamoDB, with a suggestion to build a RAG application on it.
Palworld game data was selected as the corpus afterward.

Two findings during design review undermined this.

**1. The domain is structured, not unstructured.**

The concept sketch's own example query — *"Where is an unraidable coal spot for level 20?"* —
decomposes cleanly into a structured predicate:

```
resource = 'Coal' AND min_player_level <= 20 AND danger_rating = 'low'
```

Approximate nearest-neighbor search over an embedding of that sentence is strictly worse
than evaluating the predicate: it is non-deterministic, cannot express the numeric
comparison, cannot rank by map proximity, and can return a level-45 node ranked first
because the surrounding prose happened to be similar.

This was confirmed concretely against the AWS documentation, which states that vector
index filter conditions **support exact-match values only** — range conditions such as
`BETWEEN` are not supported. The sketch's `min_player_level <= 20` filter was not
expressible. An over-fetch-and-filter-in-application workaround was designed, then
recognized as a symptom rather than a fix: we were reaching for a workaround precisely
because the retrieval mechanism could not express the query.

**2. The corpus is far too small for the technology's value proposition.**

Generously estimated at low thousands of rows. DynamoDB vector search is engineered for
horizontal scale to trillions of vectors and for eliminating sync pipelines between an
operational store and a separate vector database. Neither property binds at this size.
At a few thousand items an in-memory scan is faster than the network round trip.

Separately, the latency budget ([01-architecture.md](../01-architecture.md) §6) showed
retrieval at **under 0.5%** of end-to-end time. The sketch's emphasis on single-digit
millisecond database latency was optimizing a rounding error.

Once the premise was set aside and the problem examined directly, the query taxonomy
([00-overview.md](../00-overview.md) §3) showed three of four query classes to be
deterministic, and one — breeding — to be a **graph traversal** that no similarity search
can perform at all.

## Decision

Drop vector search and the RAG framing. Store game data as versioned local files loaded
into memory, queried with typed deterministic functions.

Retain no vector index. The only genuinely unstructured field in the dataset is
`Pal.partner_skill`, which does not justify a vector store; if fuzzy capability search
over it is wanted later, a local embedding index over a few hundred short strings is
sufficient and requires no external service.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Hybrid router** — structured queries for factual classes, vector search for fuzzy ones | Genuinely defensible, and was the recommendation while the DynamoDB premise stood. Once the premise was dropped, the vector half served only `partner_skill`, which does not justify the dependency. |
| **Vector-first** — everything through `SearchVectors` with app-side filtering | Known-wrong answers on level and location queries. Violates the zero-fabrication criterion. |
| **Keep DynamoDB for operational data, no vectors** | Retains a network hop and cloud dependency for data that is static, tiny, and local. No benefit. |

## Consequences

**Positive**
- Deterministic, exactly correct answers on Q1–Q3
- Retrieval latency and cost drop to approximately zero
- No cloud data dependency; data versioned in git with the code
- Trivially unit-testable execution layer
- Breeding becomes expressible at all

**Negative**
- Abandons the original technology showcase motivation
- Game data must be redistributed with the application rather than centrally hosted, so a
  patch requires a data refresh and release
- Fuzzy capability search over `partner_skill` is not available without future work

**Neutral**
- If the corpus later grows by orders of magnitude, or multi-user hosting becomes a goal,
  this decision should be revisited. Neither is currently in scope.
