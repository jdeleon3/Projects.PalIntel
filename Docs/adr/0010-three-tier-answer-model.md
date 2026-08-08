# ADR-0010 — Three-tier answer model

**Status:** Accepted
**Amends:** [ADR-0002](0002-llm-as-router.md)

## Context

The design originally covered four query classes under a binary rule: facts are
deterministic, and one advisory class (base siting) permits synthesis. Scope then expanded
to include general game questions, boss counter recommendations, and progression advice.

The new classes do not fit the binary.

- *"Which of my Pals counter Zoe & Grizzbolt?"* is **deterministic computation** — boss
  elements × effectiveness matrix × owned Pals — but its output is a *recommendation*, and
  the reasoning behind the ranking is worth explaining in prose.
- *"What should I research next?"* is deterministic **candidate generation** (tech
  unlockable now, minus tech taken) followed by subjective ranking against a goal.
- *"What does the Artisan trait do?"* is genuinely open. No schema, no candidate set —
  prose, retrieved and synthesized.

Forcing these into "fact" would overstate confidence in the advisory half. Forcing them
into "generative" would surrender determinism where it is available and cheap.

## Decision

Three tiers, distinguished by **how much a language model is permitted to contribute**:

| Tier | Classes | LLM role | Card |
|---|---|---|---|
| **1 — Fact** | Q1 resource, Q2 spawns, Q3 breeding | Routing only. Never touches a value. | Authoritative |
| **2 — Computed advice** | Q4 base siting, Q5 counters, Q6 progression | Orders and explains a deterministically generated candidate set. **May not add to it.** | Recommendation |
| **3 — Open knowledge** | Q7 general questions | Synthesizes over retrieved corpus text, with citations | Reference, sourced |

**Tier 2's defining constraint** is the candidate set. Candidates are computed by
deterministic code. The model may reorder them and write the rationale; it may not
introduce one. This is enforced mechanically — model output is validated against the
computed set and unrecognized entries are discarded, not trusted.

Tiers are **visually distinct** on the card, so the reader can tell fact from
recommendation from reference at a glance without reading carefully. A player mid-combat
does not parse hedging language; they parse layout.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Keep the binary; classify new tiers as generative** | Surrenders determinism in Q5 and Q6, where the answer is exactly computable. The elemental matrix is nine values — there is no reason to approximate it with a model. |
| **Keep the binary; classify new tiers as fact** | Overstates confidence. "Which Pal is best" involves genuine judgment about playstyle that the scoring function does not capture. |
| **Per-class rules, no tiers** | Seven bespoke policies. Tiers give three, applied consistently, with the card treatment falling out automatically. |

## Consequences

**Positive**
- Determinism is preserved wherever it is available, not just where it was convenient
- The candidate-set constraint gives Tier 2 a *mechanically enforceable* invariant rather
  than a stylistic guideline
- Card treatment derives from tier, so confidence signalling is consistent by construction
- New query classes are classified on one axis rather than designed from scratch

**Negative**
- Three card treatment families to design and maintain instead of two
- Tier 2 requires a validation step between model output and rendering — real code, real
  tests, in every Tier 2 path
- Tier boundaries need judgment for future classes; the tie-breaker is *"is there a
  computable candidate set?"* — if yes, Tier 2, not Tier 3

**Neutral**
- The core invariant is unchanged and undiluted: **coordinates, stats, and breeding pairs
  never originate from a model.** Tiers describe what surrounds those values, not whether
  the values themselves are trustworthy.
