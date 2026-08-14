# ADR-0018 — A local output medium, exclusive of Discord

**Status:** Accepted
**Relates to:** [ADR-0003](0003-local-first-process.md) (names this as a contemplated
Phase 5 capability), [ADR-0006](0006-templated-cards.md) (why the answer here is not
literal token streaming), [ADR-0012](0012-dual-input-channels.md) (voice and text share
one pipeline — unchanged by this)

## Context

Discord is a hard requirement today, not an optional path. `Config.load()` raises if
`discord.token` or `discord.channel_id` is empty — the bot cannot start without both,
even in text-only, no-voice configurations. A player who does not have or does not want
Discord cannot run this project at all.

Voice input is already Discord-independent: `voice.source = "mic"` captures from the OS
microphone directly and was never routed through Discord (that path only exists for
`voice.source = "discord"`, itself optional). The gap is narrower than "no Discord
support" suggests — it is specifically **typed input and all output**, both of which
assume a Discord channel exists.

The console (`palintel.ui`) already exists as a separate, token-gated, loopback-only web
server, deliberately independent of the bot process — Docs/console.md states this
explicitly: *"the job it has to do best is the one where the bot is not running."* It
already has a hardened threat model for exactly the risk a second local listener would
reintroduce (any page open in the same browser can reach `127.0.0.1`, so loopback binding
alone is not a defence — see that doc's Security section).

## Decision

**A `[output]` config section with `medium = "discord" | "local"`, exclusive — never
both at once.** One active surface per run, chosen deliberately over letting local and
Discord run simultaneously: a player watching a local chat page while a housemate reads
the same answers in Discord is two sources of truth for "what did it say", and this
project's whole discipline is refusing exactly that kind of split signal.

**Local mode adds a Chat tab to the existing console, not a second server.** One URL, one
token, one already-audited security model. The bot does not host its own web page.

**Live delivery is a bot-written event file, tailed by the console and relayed to the
browser over Server-Sent Events — not literal token-by-token streaming.** Tier 1 and
Tier 2 answers are computed the instant `execution.py` finishes; nothing about them is
generated token by token, and animating a typewriter reveal over already-complete output
would visually imply generation happening where ADR-0006 specifically removed it. What
*is* honest and worth pushing live: stage progress (`activity.py` already instruments the
moment-events — wake, heard, answered, declined, failed — and one small new event closes
the remaining gap, a live "now routing" marker) and messages appearing in the conversation
as they complete — the real shape of "dynamic", not a borrowed one from chat products
whose answers actually are generated live.

**Query submission is a pending-query file the bot polls, not a socket the bot listens
on.** Considered and set aside rather than ruled out — see Alternatives.

**The Chat tab is read-only when the bot subprocess is not running, and this falls out of
the mechanism rather than needing its own code path.** With nothing being tailed live,
the tab reads the same persisted file the same way the Sessions tab already reads past
capture logs — history, not a special disconnected-state UI.

**Local mode is single-user by construction.** Discord's identity problem — whose
question was this, for conversation memory and spend attribution — does not exist here:
there is one browser tab, on one machine, talking to one bot. `who` can be a fixed local
identity rather than solving multi-speaker attribution a second way.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **The bot hosts its own local web page** | A second URL, a second token, and a second copy of the console's already-solved threat model (any local page can reach any local port; "loopback only" was never the actual defence). The console already exists and already does this correctly. |
| **Local and Discord simultaneously** | Two rendered surfaces for one answer is a split source of truth, which is the class of problem this project refuses to ship elsewhere. Exclusive keeps "what did it say" singular. |
| **Token-by-token streaming of the answer text** | Tier 1/2 cards are complete the instant they are computed; there is nothing to stream. Simulating generation over finished, non-generated output would visually misrepresent the one invariant this whole project is built to prove — that the card was computed, not written by a model. |
| **A loopback socket the bot listens on, for query submission** | Not ruled out, deferred. It would duplicate the console's security model in a second listener, and bootstrapping which port it picked needs a file handoff anyway, so the socket adds a live channel on top of a file rather than instead of one. The latency it would save (roughly a poll interval, low hundreds of ms) is not perceptible against a multi-second router round trip. Revisit only if the file-based queue is measured to feel laggy in practice. |

## Consequences

**Positive**
- No new attack surface: the local medium reuses the console's existing token, `Origin`
  check, and loopback binding rather than adding a parallel security model.
- Every cross-process handoff in this feature is file-based, matching every other one in
  the codebase (config, capture, spend, the heartbeat) — inspectable with a text editor,
  and inheriting the reliability work already paid for there (never raising into the
  answer path, degrading on a full disk, surviving a torn line from a killed process).
- Chat history remains readable with the bot stopped, for free, because it is the same
  mechanism as the Sessions tab rather than a special case.
- Single-user local mode sidesteps the identity-attribution problem Discord's multi-user
  work had to solve.

**Negative**
- Delivery latency is a poll interval (low hundreds of ms) rather than true push, on both
  the answer side and the query-submission side. Accepted: negligible against the
  multi-second router call already in the budget.
- A local-only player loses Discord's in-game-overlay convenience during single-monitor
  play. Not a new trade-off — the project already treats a second monitor as an accepted
  way to read a card without alt-tabbing, for Discord users too.
- `Config` validation changes: Discord's token/channel become conditionally required
  (only when `medium = "discord"`) rather than unconditionally required, which is a
  startup-behaviour change worth its own test coverage.

**Neutral / open**
- Poll interval is a tuning constant, not a design decision, so it is a `Config` field
  (`output.poll_ms`, `output.inbox_poll_ms`) rather than a hardcoded number — shipped with
  a starting guess, correctable from a real session with no code change.
- ~~Artwork (map crops, Pal icons) needs a serving path for the local medium~~ **Built
  2026-08-14**: `GET /api/sessions/{session}/art/{filename}`, one route rather than
  the two originally sketched, since `LocalSink.attach_artwork`'s own event already
  names the file.
