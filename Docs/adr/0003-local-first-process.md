# ADR-0003 — Single long-lived local process, not serverless

**Status:** Accepted
**Supersedes:** `HighLevel.txt` §2 (Orchestration API row)

## Context

The original sketch specified "Python 3.11 / FastAPI / AWS Lambda" for orchestration
while also specifying Pycord voice capture for ingestion. These are incompatible.

Receiving Discord voice requires a **persistent** WebSocket connection to the voice
gateway plus a UDP media stream. AWS Lambda is a stateless, invocation-scoped runtime with
a hard execution ceiling. It cannot hold a voice gateway connection. The ingestion layer
and the orchestration layer as specified could not be the same runtime, and the sketch did
not acknowledge the split.

Additional context established during review:

- The consumer is a personal gaming group, not a hosted service.
- The player's machine is by definition **already running** whenever the system is needed.
- After [ADR-0001](0001-drop-vector-search-premise.md), the knowledge base is local files.
  With retrieval local, a remote orchestration tier has nothing left to orchestrate that
  the local process cannot do in-process.
- Save-file parsing ([ADR-0005](0005-save-file-player-state.md)) requires local filesystem
  access regardless.

## Decision

One long-lived Python process on the player's machine, containing voice capture,
activation, STT client, intent routing, execution, and card rendering.

The only network dependencies are the STT provider, the LLM provider, and Discord. There
is no application-owned cloud infrastructure.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Local bot + cloud API tier** — local process captures voice, posts transcript to a hosted service for the rest | Adds a network hop and cold-start latency to a 2.5s budget, plus deployment and secrets surface, for no capability gain. Save-file access would still have to be local, splitting player state across a network boundary. |
| **Fully hosted always-on** (Fargate/EC2) | Real recurring cost; works when the player's machine is off — a scenario that does not exist, since the player must be playing. Cannot reach the local save file at all. |

## Consequences

**Positive**
- Zero cloud compute cost and zero infrastructure to operate
- Lowest achievable latency: no cold starts, no inter-service hops
- Local save-file access is natural rather than an architectural problem
- Simple deployment: one process, one config file
- Audio never leaves the machine except as a matched utterance sent to STT

**Negative**
- The system works only while the player's machine is running. Accepted — that is exactly
  when it is needed.
- Other group members cannot query unless the host is running the bot. Acceptable for a
  personal tool; would need revisiting if shared use becomes a goal.
- Distribution to another user means running the process themselves, with their own
  provider credentials.

**Neutral**
- Keeps the door open to fully local operation. Phase 5 contemplates local STT and a local
  intent model, at which point the system has no network dependency but Discord itself.
