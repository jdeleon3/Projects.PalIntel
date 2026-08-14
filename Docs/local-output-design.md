# Local output — design

*Companion to [ADR-0018](adr/0018-local-output-medium.md), which records the decisions
and the alternatives rejected. This document is the "how": file formats, the console's
new tab, the config surface, and the build order. Nothing here is built yet — read
ADR-0018 first if the *why* behind a choice below isn't obvious from this doc alone.*

## 1. What exists already, and what this adds

| Piece | Status |
|---|---|
| Voice capture from the local mic | **Already Discord-independent.** `voice.source = "mic"` — no change needed. |
| `Card` → text / Discord embed | **Already medium-agnostic.** `to_text()` and `to_embed()` are two renderers over one model (`cards.py`'s own docstring). A third renderer is additive, not a rework. |
| The console (`palintel.ui`) | **Exists**, separate process, token-gated, loopback-only, reads session files and supervises the bot subprocess. This design adds a tab; it does not add a server. |
| `activity.py` stage instrumentation | **Partially exists.** Discrete moment-events (`wake`, `heard`, `answered`, `declined`, `failed`, `empty`) and separate post-hoc stage *durations* (`stt`, `route`, `post`) are both already tracked, today only surfaced in `/palintel status` and the console's Status tab as aggregates. Neither is quite "now in progress: routing" — that needs one small new event kind, not a rebuild (§3.2 note). |
| `_answer()` in `bot.py` | Discord-only today (`channel.send(embed=...)`). This design factors it behind a `Sink`. |
| Typed input | Discord-only today (`on_message`). This design adds a local path. |

## 2. Architecture

```
 Browser (Chat tab)                Console process (palintel.ui)          Bot subprocess
 ───────────────────                ──────────────────────────────         ──────────────
 type a message  ───POST──────────▶  /api/chat/send
                                       writes data/sessions/<id>/
                                       inbox/<uid>.json          ─────▶  polls inbox/, finds
                                                                          <uid>.json, calls
                                                                          Pipeline.handle(),
                                                                          deletes the file
                                                                              │
                                                                          LocalSink writes
                                                                          stage + answer
                                                                          events, append-only
                                                                              │
                                       tails data/sessions/<id>/  ◀────  data/sessions/<id>/
                                       chat.jsonl (every poll_ms)          chat.jsonl
 live update  ◀───SSE────────────    relays new lines over
                                       /api/chat/stream
```

The bot subprocess is unchanged in every other respect — same `Supervisor` start/stop/
restart, same heartbeat, same crash-log tail. This design adds one thing it reads
(`inbox/`) and one thing it writes (`chat.jsonl`), both under the session directory that
already exists for capture.

**No new listener anywhere.** The console already listens; the bot still doesn't.

## 3. File formats

### 3.1 `data/sessions/<id>/inbox/<uid>.json` — pending queries

One file per submitted query, written by the console, deleted by the bot once consumed.
The filename *is* the queue: presence means pending, absence means claimed. No offset or
cursor to maintain, unlike a shared log — a directory listing is the whole state.

```json
{"uid": "c19f...", "text": "how do I beat Anubis", "at": 1755203841.2}
```

The bot's poll loop lists the directory, processes files in `at` order (filename alone
does not sort chronologically), and removes each one immediately after handing it to
`Pipeline.handle()` — before the answer is fully rendered, not after, so a crash mid-answer
does not replay the same query forever. The query and its outcome are already durable in
`chat.jsonl` by the time anything downstream needs them again.

### 3.2 `data/sessions/<id>/chat.jsonl` — the live event stream

Append-only, same discipline `capture.py` already uses (never raises into the answer
path, tolerates a torn last line). Two event shapes:

```json
{"uid": "c19f...", "kind": "stage", "stage": "routing_started", "at": 1755203841.6}
{"uid": "c19f...", "kind": "answer", "at": 1755203843.1,
 "role": "assistant",
 "card": {"title": "How to fight Anubis", "lines": ["..."], "footer": "...",
          "colour": 63541, "has_image": false, "has_thumbnail": true}}
{"uid": "c19f...", "kind": "answer", "at": 1755203843.1,
 "role": "user", "text": "how do I beat Anubis"}
```

`stage` values reuse `activity.py`'s existing moment-events (`wake`, `heard`, `answered`,
`declined`, `failed`, `empty`) directly. The one gap: there's no live "now routing" event
today, only a `route` *duration* recorded after the fact — so a Chat tab wanting to show
"thinking…" while the router call is in flight needs one new event kind
(`routing_started`, emitted where `activity.timed("route", ...)` already fires, just
split into a start marker and the existing completion timing). Small, and it's the one
piece of new instrumentation this design actually needs, rather than something to pretend
already exists.

`has_image` / `has_thumbnail` are flags, not the bytes — see §3.3. A `user` role event is
written first (from the inbox file, echoed back so the console doesn't have to guess it
arrived) and an `assistant` role event follows once `Pipeline.handle()` returns.

### 3.3 Artwork

Card images are written beside the clips, same pattern as `<uid>.wav`:
`data/sessions/<id>/art/<uid>-image.jpg`, `data/sessions/<id>/art/<uid>-thumb.png`. The
console serves them through a new route mirroring the existing clip route:

```
GET /api/sessions/{session}/answer/{uid}/image
GET /api/sessions/{session}/answer/{uid}/thumbnail
```

Same path-traversal re-check `static_file` already does; no new pattern to invent.

## 4. The `Sink` contract

```python
class OutputSink(Protocol):
    def stage(self, uid: str, stage: str) -> None: ...
    def post(self, uid: str, card: Card, *, role: str = "assistant") -> None: ...
```

`DiscordSink` wraps today's `_answer()` body unchanged. `LocalSink` appends to
`chat.jsonl` and writes artwork under `art/` when `card.image` / `card.thumbnail` are
set. `_answer()` calls `sink.stage(...)` at the same points `activity.timed(...)` already
fires, so the two never drift apart — one instrumentation call site, two consumers.

Query intake is the mirror: `bot.py` gains a small poll loop (reusing whatever interval
`chat.jsonl`'s tail uses, see §6) that watches `inbox/` when `output.medium == "local"`,
identical in shape to how it already awaits Discord's `on_message` — same `Pipeline`,
same `PlayerState`, same capture/spend wiring, different arrival mechanism.

## 5. The console's Chat tab

Four states, and only one of them is new UI — the other three are the console's existing
patterns applied to a new data source:

| State | Trigger | Behaviour |
|---|---|---|
| **Hidden** | `output.medium != "local"` in the loaded config | Tab does not appear. Nothing to confuse a Discord user with. |
| **Live** | Tab configured for local **and** the bot subprocess is running | Input box active. SSE connection open. New events append to the message list as they arrive. |
| **Read-only** | Configured for local, bot not running | Same message list, built from a plain read of `chat.jsonl` (identical code path to the Sessions tab's history read) capped at the file's current length. Input box disabled with a plain reason — *"start the bot to send a new message"* — not hidden, so past conversation stays visible exactly where the Sessions tab already proves that's the right default. |
| **Empty** | Configured for local, no `chat.jsonl` yet | First-run state. Same as Read-only minus a message list. |

New endpoints, following `server.py`'s existing shape exactly (same `guard` middleware,
same token/Origin discipline — no new security model to write):

```
GET  /api/chat/{session}/history      # everything in chat.jsonl so far
GET  /api/chat/{session}/stream       # SSE: new lines as they're appended
POST /api/chat/{session}/send         # {"text": "..."} -> writes inbox/<uid>.json
```

## 6. Config

```toml
[output]
# "discord" (default, unchanged) or "local". Exclusive - see ADR-0018 for why not both.
medium = "discord"

# How often the console tails chat.jsonl for new events, and how often the bot polls
# inbox/ for a new query. Milliseconds. Two separate knobs, not one: the console poll
# governs perceived latency (how fast a card appears), the bot poll governs how fast a
# typed query starts being worked on - a player is more likely to notice the second one,
# since it's the gap between pressing enter and the "now routing" stage appearing at all.
# Both start at a guess (§9 said so plainly rather than pretending otherwise) and are
# configurable specifically so a session that finds 300ms feels laggy does not need a
# code change to say so.
poll_ms = 300
inbox_poll_ms = 150
```

`Config.load()` changes: `discord.token` / `discord.channel_id` become required only when
`output.medium == "discord"`. This is the one behavioural change to existing startup
validation, and it needs its own test coverage — today's `ConfigError` on an empty token
is asserted somewhere and that assertion needs to become conditional, not deleted.

Both poll intervals get a floor (a config editor should refuse `poll_ms = 0` the way it
already refuses other nonsensical values) but no hardcoded target - see §9.

## 7. Identity

Local mode is single-user by construction — one browser tab, one machine, one bot. `who`
is a fixed string (e.g. `"local"`) rather than solved per-message the way Discord's
multi-speaker attribution had to be. Conversation memory, spend, and capture all already
take a `who` parameter; nothing downstream needs to change to accept a constant one.

## 8. What this does *not* do

- **No token-level streaming.** See ADR-0018. The stage events are the honest version of
  "the bot is working on it"; the card itself always arrives whole.
- **No simultaneous Discord + local.** One medium, chosen at config time.
- **No change to voice input.** `voice.source = "mic"` already works without Discord;
  this design is entirely about typed input and all output.

## 9. Open questions

- ~~**Poll interval**~~ **Made configurable rather than guessed at build time** —
  `output.poll_ms` / `output.inbox_poll_ms`, §6. Shipped with a starting value (300ms /
  150ms) that is explicitly a guess, not a measurement, the same honesty
  `MAX_POSITION_AGE` was written down with — but unlike that constant, this one needs no
  code change to correct once a real session says whether it feels laggy.
- ~~**Multiple browser tabs open on the same console.**~~ **Decided 2026-08-14: single
  listener, by design.** Local mode is one player at one machine (§7) — a second tab is
  the same player opening a second window on themselves, not a second player. The SSE
  endpoint stays a single consumer; broadcast was never a real requirement, just an
  unasked question this doc raised on its own.
- ~~**What happens to a query already in `inbox/` when the bot is stopped
  mid-processing.**~~ **Decided 2026-08-14: acceptable loss.** The file is deleted
  before the answer is written (§3.1), so a crash between those two points loses the
  query silently rather than replaying it — the same trade-off a lost Discord message
  during a bot restart already carries, not a new risk this design introduces. Worth
  revisiting only if it turns out to happen often enough to notice, which the console
  has no way to measure and nobody has reported.

## 10. Suggested build order

1. `OutputSink` protocol + `DiscordSink` (pure refactor of today's `_answer()`, no
   behaviour change, provable by the existing test suite passing unchanged).
2. `Config`'s `[output]` section and the conditional Discord validation, with tests for
   both branches.
3. `LocalSink` writing `chat.jsonl` + `inbox/` polling in `bot.py`, no console changes yet
   — provable by reading the files directly.
4. Console: history read + SSE tail + send endpoint.
5. Console: the Chat tab UI and its four states.
6. Artwork serving (§3.3) — last, because most queries have none and the class of card
   that does (spawn locations, Pal info) can wait behind everything else being provably
   correct first.
