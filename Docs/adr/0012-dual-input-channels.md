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

## Amendment (2026-08-09) — voice is single-speaker

Voice input is the local microphone, not a Discord voice channel: Discord's DAVE
end-to-end encryption broke reception in py-cord
([pycord#3139](https://github.com/Pycord-Development/pycord/issues/3139)) and no audio
arrives at all. See [ADR-0004](0004-wake-word-activation.md).

The two-path decision stands, but its reach narrows. Voice serves the player at the
machine; **party members can no longer ask by voice** and are served by the text path
alone. For them, text is no longer the convenient second option this ADR describes — it
is the only one.

This is a genuine reduction and is recorded rather than absorbed. It is also reversible:
`SpeakerStream` still keys by speaker id, so multi-speaker voice returns as configuration
if reception is ever fixed upstream.

## Amendment (2026-08-10) — attribution is configuration, not detection

The cross-channel promise above ("a spoken question can be followed up in text") did not
hold once conversation memory shipped. Memory is per person and the text path keys on the
Discord display name, while the voice path had no identity to key on and used the literal
`"voice"` — so the same human's spoken question and typed follow-up landed in two separate
threads, and the follow-up asked for a restatement.

The microphone cannot say who spoke, so attribution is `voice.speaker` in config: name the
person at the machine and the two channels share one thread. **Left unset it stays
`"voice"` and they do not share**, which is the honest default — inferring which Discord
user is sitting at the machine would attribute speech to the wrong person in a shared
channel, and that is worse than not joining them at all. `/palintel status` reports which
of the two is in force, because unattributed voice is otherwise invisible until a
follow-up mysteriously fails.

**What this does not do** is tell two people in the same room apart. That needs speaker
diarisation from a single mixed stream, which is a different problem from the one
`SpeakerStream` solved (Discord tagged every packet with a user id, so the split was free).
It is unbuilt, and no evidence has been gathered that the case arises for this
installation — see [04-roadmap.md](../04-roadmap.md) Phase 2.
