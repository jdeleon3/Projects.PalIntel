# ADR-0007 — One entity lexicon serving three consumers

**Status:** Accepted
**Addresses:** Assumption A5 — the highest-rated accuracy risk in the system

## Context

Palworld's proper nouns are invented words: *Lifmunk, Jormuntide, Depresso, Chillet,
Faleris, Anubis, Digtoise*. General-purpose speech-to-text has no prior for them and will
mangle them — *"Lifmunk"* → *"life monk"*.

The damage compounds. A corrupted entity name poisons every downstream stage: the intent
router cannot match a tool parameter, the execution layer finds no rows, and the player
gets a decline card for a question that was asked perfectly clearly. Entity accuracy is
therefore a **ceiling** on total system accuracy, not one contributor among many.

The original sketch did not mention this at all.

## Decision

A single canonical entity lexicon — Pal names, resource names, region names, each with
aliases and a phonetic key — is the source of truth for **three** consumers:

| Consumer | Use |
|---|---|
| STT client | Keyterm boosting: bias the acoustic model toward known proper nouns |
| Lexicon corrector | Fuzzy-match transcript tokens to canonical names |
| Intent router | Generate the enums constraining tool parameters, so the model selects from known values rather than emitting free text |

This gives **three layered defenses** against the same failure: prevent it acoustically,
repair it lexically, constrain it structurally.

Correction happens once, at the corrector boundary. Every stage downstream may assume
entity names are canonical.

Matches below the confidence threshold are **not** silently coerced — the card names the
unrecognized token so the player can retry. Guessing between *Chillet* and *Chikipi* and
confidently answering the wrong question is worse than admitting the miss.

Aliases are seeded by hand from known-hard names and then **grown from observed STT
failures**: every misrecognition found during evaluation becomes a permanent alias. The
lexicon is an append-only asset that improves monotonically, which is what makes A5 a
tractable engineering task rather than an open risk.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Rely on STT accuracy alone** | No general-purpose model has a prior for these words. Fails on the hardest and most-used names. |
| **Fuzzy correction only, no boosting** | Repairs after the fact but discards acoustic information. Boosting is nearly free and strictly additive. |
| **Let the LLM router infer intended entities** | Reintroduces generative guessing into the factual path, contradicting [ADR-0002](0002-llm-as-router.md), and can silently answer about the wrong entity. |
| **Separate lexicons per consumer** | Guarantees drift. A name fixed for correction but not for boosting or enum generation stays broken in two of three places. |

## Consequences

**Positive**
- Three independent defenses against the dominant accuracy risk
- One source of truth; no drift between consumers
- Improves monotonically as failures are observed and folded back in
- Generated from the Paldeck ingest ([03-data-ingestion.md](../03-data-ingestion.md) §3.2),
  so it stays current with patches automatically

**Negative**
- The Paldeck ingest becomes a dependency of the STT layer, not just the Q2 query path —
  hence its early sequencing despite Q2 being post-v1.
- Threshold tuning is empirical: too low coerces wrong entities, too high declines valid
  queries. Calibrated in Phase 0.6 against recorded utterances.

**Neutral**
- The lexicon is a first-class versioned asset. Alias additions ship as data changes, not
  code changes.
