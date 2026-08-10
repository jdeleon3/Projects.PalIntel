# ADR-0004 — Wake-word activation over continuous transcription

**Status:** Accepted

## Context

The original sketch implied continuous capture of a Discord voice channel. A voice channel
during a gaming session is mostly **not** queries — it is conversation, game audio bleed,
and silence. Transcribing all of it means:

- Paying STT for every second the channel is open, whether or not anyone asks anything
- Streaming private conversation between friends to a third-party provider
- Solving query-versus-chatter classification downstream, where the cost has already been paid

The input constraint that motivates the project — hands captured by the game — also rules
out anything requiring reliable keyboard interaction during play.

## Decision

Two-stage local gating before any audio leaves the machine:

1. **VAD** discards silence.
2. **Wake-word detection** matches a configured phrase (default *"Hey Pal"*).

Only audio *following* a wake-word match is buffered and sent to STT. The buffer closes on
trailing silence (default 700ms) or a hard cap (default 10s).

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Push-to-talk hotkey** | Requires a keypress during play. Global hotkeys can work with the game focused, but it is one interaction short of hands-free and competes with game bindings. Worth offering as a config option, not as the default. |
| **Continuous transcription + downstream intent filter** | Highest cost, worst privacy, most false positives. Pays for classification after the expensive step rather than before. |
| **Slash-command activation** | Requires alt-tabbing to type — defeats the project's entire premise. |

## Consequences

**Positive**
- Idle cost is exactly zero
- Party conversation never leaves the machine — a privacy property, not just a cost one
- Genuinely hands-free, satisfying the constraint that motivates the project
- The wake word doubles as an unambiguous intent signal, simplifying the router's job

**Negative**
- Wake-word false negatives are silent failures: the player speaks and nothing happens.
  Mitigated by a configurable detection threshold and by `/palintel status` reporting
  recent activation counts.
- False positives cost one wasted STT + routing round trip, though they terminate cleanly
  at the router's decline path.
- Adds a local dependency (wake-word detector) and a small always-on CPU cost — negligible
  next to the game itself.

**Neutral**
- Wake-word phrase and detection threshold are configuration, allowing per-user tuning
  without code changes.

## Amendment (2026-08-09) — the audio source is the local microphone

The context above assumed the audio came from a Discord voice channel. It does not, and
cannot: Discord's DAVE end-to-end encryption broke voice reception in py-cord
([pycord#3139](https://github.com/Pycord-Development/pycord/issues/3139)), where the
connection succeeds, a sink attaches, and no audio ever arrives. That is not fixable from
this side. Input now comes from the local microphone; output is still a Discord channel.

The decision — wake-word gating before any audio leaves the machine — is unchanged, and
two of its stated consequences get *stronger*: the captured stream is now one player's
microphone rather than a shared channel, so there is less to keep local and no party
conversation to protect in the first place.

Two things above no longer describe the implementation:

- **Stage ordering.** VAD does not run before the wake word. openWakeWord scores every
  frame directly, and the amplitude floor is consulted only *after* a wake word fires, to
  decide where the utterance ends. Running VAD first would risk gating the wake word
  itself behind an energy threshold, which is the failure this ADR calls silent.
- **Party members asking by voice.** They cannot; they keep the text path. Recorded
  against [ADR-0012](0012-dual-input-channels.md), which is where the reduction lands.

`/palintel status` and its activation counts remain outstanding.
