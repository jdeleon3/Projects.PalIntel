# ADR-0006 — Templated cards, not LLM-generated

**Status:** Accepted
**Supersedes:** `HighLevel.txt` §4 step 5

## Context

The original sketch specified: *"LLM formats query result into structured JSON card
(~300ms)."* Review found this step to be the worst line in the design on three independent
axes.

**Correctness.** It routes coordinates — the values the player acts on — through a
generative model, purely for presentation. The model has nothing to add: the values are
already known and typed. It can only degrade them.

**Latency.** At 300–800ms it was the single largest controllable component of a 2.5s
budget, spent on formatting.

**Cost.** It was the dominant per-query cost, again for formatting.

A step that adds risk, latency, and cost while adding no information is not a trade-off.

## Decision

One Discord embed template per result type. Factual fields are interpolated directly from
the typed result. No model participates in rendering.

Q4 (base siting) is the sole exception: its `rationale` prose may be synthesized, because
that class is inherently advisory. Even there, coordinates and site identities come from
the retrieved set, and the card is **visually distinguished** from factual cards so the
player can tell advice from fact at a glance.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **LLM generates the full card** (as sketched) | Hallucination risk on the exact field the player acts on; largest latency and cost line; no informational gain. |
| **LLM generates prose, template holds the facts** | Costs the latency and spend of a model call to produce prose that adds nothing to *"Coal, 3 nodes, (188, 37), level 20+, low danger"* — which is already the clearest possible form. Reconsider only if a query class emerges where prose genuinely carries information. |

## Consequences

**Positive**
- Removes 300–800ms from every query
- Removes the dominant per-query cost
- Card output is deterministic — testable with exact assertions, and identical for
  identical inputs
- Templates can be tuned for second-screen legibility (field count, contrast, colour
  coding) with immediate, predictable results

**Negative**
- Every new result type needs a hand-written template. Acceptable: there are four query
  classes, and template design is where at-a-glance legibility is actually won.
- Cards cannot adapt phrasing to how the question was asked. Not a loss — a HUD card read
  mid-combat should be terse and *consistent*, so the player learns where to look.

**Neutral**
- Card templates become a design surface in their own right. Field ordering, colour, and
  density are legibility decisions to be validated by reading cards on the second screen
  during real play ([04-roadmap.md](../04-roadmap.md) Phase 1), not styling preferences.
