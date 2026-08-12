# PalIntel — Design Documentation

A Palworld assistant in Discord. Ask by voice while playing — or by text any time — and a
structured answer card appears in a Discord channel, readable via the in-game overlay, a
second monitor, or a phone.

## Why voice in, Discord out

Mouse and keyboard are captured by the game client during play, so voice is the only free
input channel *while playing*. Output goes to a Discord text channel, which the in-game
overlay renders without unfocusing the game. Text input is also supported, since the
keyboard constraint applies only during active play.

## The shape of the design

Seven query classes across three tiers, distinguished by how much a language model is
allowed to contribute:

| Tier | Classes | LLM role |
|---|---|---|
| **1 — Fact** | Resource coords, Pal spawns, breeding paths | Routing only |
| **2 — Computed advice** | Boss counters, base siting, next tech | Orders and explains a computed candidate set |
| **3 — Open knowledge** | General game questions | Grounded synthesis over a retrieved corpus, cited |

One invariant holds throughout: **coordinates, stats, and breeding pairs never originate
from a model.**

## Document index

| Doc | Contents |
|---|---|
| [00-overview.md](00-overview.md) | Problem, query taxonomy, three-tier model, scope, success criteria |
| [01-architecture.md](01-architecture.md) | Components, tool contract, sequence flows, latency budget, failure modes |
| [02-data-model.md](02-data-model.md) | Entity schemas, both stores, the entity lexicon |
| [03-data-ingestion.md](03-data-ingestion.md) | Sourcing, normalizing, embedding, and validating the datasets |
| [04-roadmap.md](04-roadmap.md) | Phased build plan with exit criteria |
| [test-plan.md](test-plan.md) | **What is untested or needs retaking in game**, with the wording to say and what to expect |
| [play-session-protocol.md](play-session-protocol.md) | The latency-grading script and the ground-truth walk record. Superseded for everyday use by `test-plan.md` |
| [breeding-verification.md](breeding-verification.md) | The ADR-0008 sheet, generated and waiting on hatched eggs |
| [adr/](adr/) | Architecture Decision Records, including the amendment chain |

## Reading order

New to the project: `00` → `01` → `04`. Implementing: add `02` and `03`. The ADR log
explains *why* the design departs from the original sketch — read it before proposing
changes that reintroduce discarded approaches.

## Status of `HighLevel.txt`

`HighLevel.txt` is the original concept sketch and is **superseded** by these documents.
It is retained for provenance. Three of its core technical claims did not survive review —
see [ADR-0001](adr/0001-drop-vector-search-premise.md),
[ADR-0003](adr/0003-local-first-process.md), and
[ADR-0006](adr/0006-templated-cards.md).

The design has since been through a second revision expanding scope from four query classes
to seven, adding conversational and general-knowledge capability. Two earlier ADRs were
**amended rather than reversed** — see the amendment chain in [adr/README.md](adr/README.md).
