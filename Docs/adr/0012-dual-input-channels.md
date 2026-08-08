# ADR-0012 — Voice and text input share one pipeline

**Status:** Accepted

## Context

The project's motivating constraint is that mouse and keyboard are captured by the game, so
voice is the only free input channel *while playing*. That constraint is real, but it is
narrower than it first appears: it applies during active play, not to the whole lifecycle
of the tool.

The output is a Discord text channel. Users are already reading it there. Typing a question
into that same channel — while dead, in a menu, planning between sessions, or on a phone
away from the machine — is natural and costs the user nothing.

Separately, a practical development problem: every intent-routing evaluation would otherwise
require speaking into a microphone. That makes the eval loop slow, hard to automate, and
impossible to run in CI.

## Decision

Accept input from both voice and text. They converge at the lexicon corrector and share
everything downstream.

```
voice → wake word → STT ──┐
                          ├─→ lexicon corrector → intent router → execution → card
text  (channel message) ──┘
```

Text skips only wake-word detection and STT. Identical routing, identical tools, identical
cards.

Text intake lands in **Phase 1**, not later. It is a small addition once routing exists, and
it is what makes Phase 1's own exit criteria measurable without a microphone.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Voice only** | Truer to the original framing, but the framing was about *play-time input*, not about excluding text. Discards the cheapest testing path for no benefit. |
| **Text only** | Abandons the constraint that motivates the project. |
| **Text as a later addition** | Same eventual cost, but Phase 1 through Phase 3 would be developed and evaluated without it — the phases that benefit most. |

## Consequences

**Positive**
- Intent routing becomes testable without audio: eval sets are text files, runnable in CI
- Lets STT accuracy (A5) be isolated from routing accuracy during evaluation, since the same
  utterance can be fed as text and as speech and the results compared. This turns "the
  system got it wrong" into a specific attribution.
- Usable when not actively playing — planning, from a phone, between sessions
- Users who cannot or prefer not to use voice are served

**Negative**
- Two intake paths to maintain, including message filtering (which channel messages are
  directed at the bot?) and loop prevention (never respond to itself)
- Text lacks the wake word's unambiguous intent signal, so the router sees more off-domain
  input. Handled by the existing decline path.

**Neutral**
- Text has a tighter latency target (p95 ≤ 1.5s vs 2.5s) since it omits endpointing and STT.
  Not a stricter requirement — the same pipeline with two stages removed.
- Per-user conversation memory ([ADR-0013](0013-conversation-memory.md)) spans both channels:
  a spoken question can be followed up in text, and vice versa.
