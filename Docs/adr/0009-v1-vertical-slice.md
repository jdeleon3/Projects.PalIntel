# ADR-0009 — Q1 resource lookup as the v1 vertical slice

**Status:** Accepted

## Context

Four query classes, one pipeline. The pipeline itself — wake word → STT → lexicon
correction → intent routing → typed query → templated card → overlay — is unproven end to
end and carries most of the project's integration risk.

The choice is which query class to build first, knowing the first one pays the full cost
of building the pipeline while subsequent ones are incremental.

## Decision

**Q1 (resource location)**, narrowed further to a single resource type end to end before
generalizing across resource types.

Q1 is the only class that exercises every pipeline stage while introducing **no**
additional algorithmic or data complexity:

| Class | Pipeline coverage | Added complexity |
|---|---|---|
| **Q1 Resource** | Full | None — flat table, filter, sort |
| Q2 Pal location | Full | Full Paldeck + spawn ingest; hardest lexicon cases |
| Q3 Breeding | Full | Graph search + unverified A3 + hardest card layout |
| Q4 Base siting | Full | Generative synthesis + curated corpus + advisory card design |

Q1's data model is a flat table with a filter and a distance sort. If the slice fails, the
failure is unambiguously in the pipeline rather than confounded with graph search or
generative output.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Q3 breeding first** | Highest-value and most differentiated, and tempting for that reason. But it front-loads the hardest data sourcing, the only real algorithm, an unverified assumption, and the hardest card layout — onto an unproven pipeline. Failures would be ambiguous between integration and algorithm. |
| **Q2 Pal location first** | Reasonable middle ground and the best test of the lexicon. Rejected because it blocks the slice on full Paldeck ingest, and lexicon risk is already directly addressed by Phase 0.6. |
| **Q4 base siting first** | The only generative path — the least deterministic possible way to validate a pipeline whose central claim is determinism. |
| **All four in parallel** | Pipeline is unproven; parallel work would be rebuilt after the first integration lesson. |

## Consequences

**Positive**
- Fastest path to a demonstrably working end-to-end system
- Pipeline failures are isolated from domain-logic failures
- Q1 is the highest-frequency query in real play — genuinely useful on day one, so the
  Phase 1 exit criterion of *"used during a real play session"* is a real test
- Defers full Paldeck ingest, keeping Phase 1 data work small

**Negative**
- The most differentiated feature (breeding) is deferred to Phase 3
- Single-tool routing does not exercise disambiguation between query classes. Deliberate —
  disambiguation arrives in Phase 2 with a widened eval set, when there is something to
  disambiguate.

**Neutral**
- Phase 1 registers exactly one tool with the router. The multi-tool case is a Phase 2
  concern with its own accuracy target.
