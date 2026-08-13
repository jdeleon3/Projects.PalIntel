# ADR-0005 — Read player state from the local save file

**Status:** Accepted
**Depends on:** Assumption A2 (Phase 0.3)

## Context

Half the query taxonomy is materially less useful without knowing the player's own state.

- *"How do I breed Anubis?"* — a generic answer is a toy. The actionable answer is a chain
  starting from the Pals **you already own**.
- *"Where should my 2nd base go?"* — meaningless without knowing where base 1 is.
- *"Where's the nearest coal?"* — "nearest" requires a reference point.
- Level gating on Q1 results requires the player's level.

Palworld writes save data to local disk, and community tooling for parsing it exists. The
system already runs locally ([ADR-0003](0003-local-first-process.md)), so the file is
directly reachable.

## Decision

A save watcher monitors the Palworld save directory and parses player state — owned Pals,
base coordinates, player level — into a `PlayerState` object, refreshed on file change.

`PlayerState` is injected by the dispatcher into tool parameters (`near`, `owned`,
`max_player_level`) rather than extracted from the utterance. "Nearest" resolves against
live save data, not against something the player has to say aloud.

**Read-only, without exception.** The application never writes to the save directory.
`PlayerState` is held in memory and never persisted by this application.

### Amendment, 2026-08-13 — one mapping is persisted, and it is not player state

Multi-user needs to know which Discord user is which in-game player, and nothing in either
system knows about the other. That mapping is written to `data/players.json`
(`palintel/identity.py`), which is gitignored because it holds Discord user ids.

This does not weaken the rule above. What is persisted is a **claim a person made about
themselves** — "I am Rui" — not anything read out of the save. No position, roster,
technology set or coordinate is written anywhere, and the save directory is still never
written to.

The alternative was holding bindings in memory, and it was rejected on use rather than on
principle: rebinding everyone after each restart is how a feature stops being used.

**One player state field is still never persisted and never guessed.** A speaker the bot
cannot place resolves to `None`, and every card answers about the world and says so.
Falling back to "the most recently written save" would make the feature work for whoever
autosaved last and silently lie to everyone else — see
[`Docs/multi-user-design.md`](../multi-user-design.md) §6.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Manual declaration** (`/setlevel 34`, `/addpal Anubis`) | Requires typing — the thing the project exists to avoid — and drifts out of sync with reality. Retained as a **fallback** if A2 fails, and as an override. |
| **Stateless / generic answers** | Simplest, but degrades Q3 to a toy and Q4 to near-useless. Retained as the graceful-degradation path when no save file is found. |
| **Read game memory or intercept traffic** | Rejected outright. Resembles cheat tooling, risks anti-cheat consequences for the player, and is far more fragile than a file on disk. |

## Consequences

**Positive**
- Zero maintenance burden: state is always current with no player effort
- Makes Q3 and Q4 genuinely actionable rather than illustrative
- Enables proximity sorting and level gating without the player stating either aloud
- Reinforces the local-first architecture — this is a capability a hosted service could
  not offer

**Negative**
- **Save format is not a stable contract.** Palworld patches can change it, breaking
  parsing. Isolated behind a `SaveParser` interface with an explicit
  "state unavailable" degradation path, and surfaced by `/palintel status`.
- Adds a dependency on community save tooling, with its own maintenance risk.
- Parsing large saves may be slow; parse on change with debounce, never in the query path.

**Neutral**
- The read-only constraint is an architectural invariant, not a guideline. Any future
  feature implying a save write requires a new ADR and should be presumed rejected.
- Save file paths are machine-specific and go in local config, which is gitignored.
