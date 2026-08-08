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
