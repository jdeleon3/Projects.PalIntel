# ADR-0008 — Derive the breeding graph from combination rank

**Status:** **Provisional** — accepted pending verification of assumption A3 (Phase 0.4)

## Context

Q3 (*"How do I breed Anubis?"*) is a shortest-path problem over a combination graph, from
the Pals the player owns to a target. It is the only genuine algorithm in the system and
the query class most clearly beyond the reach of retrieval-based approaches.

Building it requires the graph's edges — which parent pairs produce which children. With
200+ Pals, the explicit combination table is on the order of tens of thousands of pairs.

Palworld's breeding is understood to be **deterministic from a single per-Pal
combination rank**: the child of two parents is the Pal whose rank lies closest to the
parents' average, subject to a table of special-case pairs that override the general rule.

If true, the entire graph compresses to **one integer per Pal plus an exception table** —
a few hundred rows total, from which every edge is derived on demand.

**This is an unverified assumption about game mechanics.** It is stated here as the design
basis, not as established fact.

## Decision

Model breeding behind a protocol so that the derivation strategy is a swappable
implementation detail:

```python
class BreedingModel(Protocol):
    def child_of(self, a: PalName, b: PalName) -> PalName: ...
    def parents_producing(self, target: PalName) -> Iterator[tuple[PalName, PalName]]: ...
```

- `RankBasedBreedingModel` — derives edges from combination rank + exceptions. **Primary.**
- `TableBasedBreedingModel` — explicit scraped combination table. **Fallback if A3 fails.**

`breeding_path` implements BFS against the protocol only, and is unchanged regardless of
which model backs it.

**Verification gate (Phase 0.4):** derive the full combination table from ranks and check
it against ≥ 100 known combinations from an independent source. Require **100% agreement**
outside the declared exception table. Anything less means the rank model is incomplete and
the fallback applies.

Partial agreement is treated as failure, not as a tunable. A breeding model that is right
95% of the time produces confidently wrong chains 1 in 20 times, and the player discovers
this only after investing eggs and hours.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Scrape explicit combinations unconditionally** | Tens of thousands of rows to acquire, validate, and re-verify every patch. Correct but expensive — retained as the fallback, not the default. |
| **Derive from ranks with no verification gate** | Ships an unverified game-mechanics assumption into the one query class where errors cost the player real time. |
| **Query a third-party breeding calculator at runtime** | Adds a network dependency and a third party to the hot path, contradicting [ADR-0003](0003-local-first-process.md), and leaves correctness unowned. |

## Consequences

**Positive**
- If A3 holds: the breeding dataset is a few hundred rows, trivially maintained per patch
- Edges are derived, so a new Pal needs only its rank rather than N new combination rows
- The protocol boundary means A3 failing costs ingestion effort, not a redesign

**Negative**
- Provisional status: Phase 3 cannot be fully planned until Phase 0.4 resolves
- If A3 fails, breeding data acquisition and per-patch maintenance grow substantially

**Neutral**
- The exception table's size is itself the signal. A handful of exceptions confirms the
  rank model. Hundreds means the "rule" is really a table wearing a disguise, and the
  fallback is the honest implementation.
