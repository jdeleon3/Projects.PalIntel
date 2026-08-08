# ADR-0013 — Short per-user conversation memory

**Status:** Accepted

## Context

Once the system spans seven query classes and answers open questions, users will naturally
ask follow-ups:

> — *"Which of my Pals counter Zoe & Grizzbolt?"*
> — *"What about the next tower?"*
> — *"Which of those is closest to my base?"*

Without prior-turn state, every follow-up must be fully restated. That is tolerable for a
command dispatcher and wrong for something described as a chatbot — and restating a full
question by voice, mid-play, is exactly the friction the project exists to remove.

Against that: conversation state is a well-known source of subtle failures. Stale context
resolves a pronoun to the wrong entity, and the resulting card looks entirely authoritative
while answering a question nobody asked.

## Decision

A per-user, per-channel ring buffer of recent turns, TTL-bounded. Defaults: **4 turns,
5 minutes**.

**Store resolved state, not raw transcripts:**

```python
@dataclass
class ConversationTurn:
    user_id: str
    tool_called: str | None
    entities: dict[str, str]      # resolved canonical entities
    result_summary: str           # compact, not the full card
    timestamp: datetime
```

This matters. Follow-ups resolve against *structured facts already extracted* rather than
against prose the router must re-parse. The context window stays small, resolution is
inspectable, and a wrong resolution is traceable to a specific stored entity.

**Scope and clearing**
- Per user, not per channel — concurrent askers do not contaminate each other
- Spans input channels: ask by voice, follow up by text
- Cleared by TTL, by `/palintel reset`, or on detected topic change
- Expired context is **not** silently ignored: a follow-up referencing it asks for
  restatement rather than guessing the referent

**Supporting data.** `Boss.tower_order` exists specifically so *"the next tower"* resolves
deterministically from the prior turn's boss rather than being inferred
([02-data-model.md](../02-data-model.md) §4.1). Where a follow-up can be resolved by
structured data instead of model inference, it should be.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Stateless single-turn** | Simplest, no context-poisoning failure modes. Rejected because it makes natural follow-ups impossible, and restating full questions aloud mid-play is precisely the friction being designed out. |
| **Full transcript history in context** | Larger context, higher cost, more poisoning surface, and no benefit over resolved entities — the router needs *what was asked about*, not *how it was phrased*. |
| **Unbounded session memory** | Stale context grows more dangerous with age. A 40-minute-old referent is far likelier to be wrong than a 40-second-old one, and a TTL is the cheapest possible guard. |

## Consequences

**Positive**
- Natural follow-ups work, which is what distinguishes a chatbot from a command line
- Resolved-state storage keeps context small, cheap, and inspectable
- Per-user scoping supports shared channels without cross-talk
- TTL bounds the blast radius of any stale context

**Negative**
- A new class of failure: wrong referent resolution. Mitigated by TTL, by explicit
  restatement prompts on expiry, and by an eval set specifically covering follow-ups
  ([04-roadmap.md](../04-roadmap.md) Phase 2).
- Routing evaluation becomes stateful — the eval set needs multi-turn sequences, not just
  independent utterances
- TTL and buffer depth need empirical tuning

**Neutral**
- Memory is in-process and never persisted to disk, consistent with the privacy posture in
  [01-architecture.md](../01-architecture.md) §9.
