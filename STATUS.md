# Status

**Read this first, then [`Docs/04-roadmap.md`](Docs/04-roadmap.md) for the detail behind
any line.** This file is the two-minute orientation; the roadmap is the record of how each
number was arrived at.

*Last updated 2026-08-10. Branch `design-and-phase0`; `main` is **101 commits** behind and has
never been promoted.*

---

## Where the project is

| Phase | State |
|---|---|
| 0 — De-risk | **Closed.** A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ · A7 ◐ · A1 ⊘ retired · A5 ✅ accepted at measured behaviour |
| 1 — Q1 resource lookup | **Closed 2026-08-10.** Latency accepted at measured behaviour, carried forward |
| 2 — Q2 Pal spawns + memory | **Closed 2026-08-10** |
| Card artwork + drops | **Shipped.** [ADR-0017](Docs/adr/0017-card-artwork-from-game-assets.md) Accepted |
| 3 — Q3 breeding + Q5 counters | Not started. Gated on A3, which is de-risked but unbuilt |
| 4 — Q6 tech + Q4 base siting + Q7 corpus | Not started |

## What answers a question today

| Class | Ask | Notes |
|---|---|---|
| Resource location | *"where's the nearest coal"* | + map crop, + "Also drops from" |
| Pal location | *"where can I find Chillet"* | + map crop, + icon, + "Ranch:" |
| Pal drops | *"what does Vanwyrm drop"* | split ordinary / alpha-only / level-banded |
| Item source | *"who drops Flame Organ"* | 151 items, enum-only (not in the lexicon) |

Voice in via the local microphone, text in via the channel, cards out to Discord.
One consolidated `answer_query` tool routes all four.

---

## What is measured, and what only looks measured

This distinction is the point of the file. Several numbers below are **accepted at
measured behaviour** rather than at their original bar, and two things are not measured
at all.

### Measured

| | |
|---|---|
| Router accuracy | 88.8% exact, 3.9% wrong entity, on the shipping config |
| Consolidated vs per-class tools | Indistinguishable, McNemar p = 0.73; 21% faster median |
| Cost | $0.0036/request — 75% thinking tokens, 12% schema |
| Map render | 7.8 ms p50, 25.5 ms p95, ~65 KB, entirely off the graded path |
| Icon coverage | 285 of 286 Paldeck entries |

### Not measured — and cannot be, without you

| Gap | Why it is stuck |
|---|---|
| ~~`art_post` p95~~ | **Measured**: 531ms p50, 1,157ms p95 over 70 attachments. Edit-in delivery holds. |
| ~~**Do markers land on the actual rock?**~~ | **Closed 2026-08-11.** Ore, stone, wood, paldium walked against the regenerated table — nearest *and* further markers on each card, inside and outside the base — plus quartz at (-53,-960) and (-52,12), ~551 and ~573 units out on different bearings. Near-field and far-field, five resources, separate clusters. |
| **Does `item_source` work?** | All 240 eval recordings predate the class. Ten queries were *asked* on 2026-08-11 and all routed to the model as designed — but **only Chillet's card was read back**, so routing is confirmed and correctness is not. |
| ~~The Phase 1 latency criterion~~ | **Measured 2026-08-10 and FAILED**: voice p95 4.2s / 2.5s, text 2.0s / 1.5s. Not a tuning problem — p95 sits in the model population whenever a shipped class has no fast path. See the roadmap. |

All four are in [`Docs/play-session-protocol.md`](Docs/play-session-protocol.md); three
were closed by the 2026-08-10 and 2026-08-11 sessions. **`item_source` is the last, and
what it needs now is reading, not asking** — the cards from block 6 either name the right
Pals or they do not, and nobody has looked.

One note on how the walk was done: the four nearest nodes sit inside a base whose Pals keep
them mined out, so some were confirmed by position rather than by a deposit being present —
a property of this base's placement, not something the project models against. The further
markers on each card were walked too, outside the base, with deposits standing there.

### Known-uncalibrated

- `min_player_level` / `danger` shipped **uncalibrated** — the rule asks for ~20 nodes of
  known difficulty read in-game and has had none.
- Tree-region coordinates go through a transform fitted only on MainMap landmarks.

---

## Next

1. ~~Play session~~ — **the parts that needed playing are done.** The 2026-08-10 session
   graded latency (87 answered, 30 of each kind) and measured `art_post`; the 2026-08-11
   [§Short run](Docs/play-session-protocol.md#short-run--the-30-minute-version) closed the
   marker walk and exercised the drop classes. What is left is **reading cards already in
   the channel** — the six `item_source` answers, and block 7 on the artwork. Scrollback,
   not a session. The seven judgement calls are editorial and can wait for Phase 3's first
   play test.
2. ~~Dungeon spike~~ — **done.** The link exists, but the feature shrank on contact with
   play; see the backlog.
3. **Phase 3** — Q3 breeding and Q5 counters, **and now the next thing to actually do.**
   Gated on A3, which is de-risked but unbuilt.

## Decisions waiting on you

| | |
|---|---|
| **Coal coverage** | 552 → 308 clusters. Cave coal is most of Palworld's coal and can no longer be asked for. Accept, or promote the dungeon feature? |
| `maps` and `icons` | One flag pair, two features with very different risk. Separable. |
| Card density | Resource cards gained "Also drops from", Pal cards gained "Ranch:". Editorial. |
| `main` is 101 commits behind | Never promoted. Fine, but a deliberate choice rather than an accident. |

## Backlog

- **Find dungeons near me** — spiked, viable, and **thinner than it looked**. The 18
  permanent "Sealed Realm" arenas are already marked on the in-game map; the 13 random
  sites hold a dungeon only ~67% of the time. Does not recover the lost cave coal.
- **The drop fast path's second-entity guard depends on STT** — deferred 2026-08-11, not
  resolved. `routing.py:457` defers a two-Pal drop question to the model only when the
  *second* Pal clears the lexicon floor, so when speech damages that name the guard does
  not fire and the fast path answers a two-answer question from its single slot. Observed:
  typed *"Astralym and Mycora"* → model, 1.7s; spoken *"Astralym in Makora"* → **0.1s
  fast**, twice. The mechanism is confirmed in the code; **what was never checked is
  whether those runs produced one card or two**, and one card is the whole finding. Cheap
  to settle — ask it once with the second name mangled and count the cards. Do that before
  proposing a threshold change, since the fix shape ("a Pal-kind near-miss below floor
  defers rather than claims") is a guess until the card count exists.
- **STT accuracy on this speaker's actual speech** — the largest untouched lever. Play on
  2026-08-11 produced `Vanworm`, `man worm`, `Makora`, `Pantlion`, `Disneyland Ball Drop`
  (Lamball) and `Wooddrop Spones`, and the damage is not only cosmetic: it decides the
  routing path, it is correlated with the worst latencies (the two most mangled
  transcripts were the two slowest, 3.7s and 4.3s), and a *slightly* wrong token is worse
  than a badly wrong one — the fast path claims it instead of deferring. Unexplored
  options, cheapest first: hotword/`initial_prompt` biasing beyond the five hoisted
  resources, a larger Whisper model now that STT is local and free
  ([ADR-0015](Docs/adr/0015-local-gpu-stt.md) removed the per-second billing that argued
  against it), and speaker-specific tuning. **Measure against the 236 recorded utterances
  before and after** — this is exactly the kind of change that feels better and is not.
- **Discord voice receive** — upstream-blocked on DAVE; party members cannot ask by voice
- **Authoritative ranch source** — currently the only community-sourced dataset in the
  project ([ADR-0014](Docs/adr/0014-game-files-as-source.md) amendment)
- Node appearance art — needs an offline mesh render; the item icon was tried and dropped
- Lexicon growth from observed STT failures; corpus coverage; patch-refresh drill

---

## Things that shipped wrong and were caught late

Kept because each is a class of error worth recognising again, not a list of scars.

- **16.4% of the node dataset was dungeon interiors** presented as overworld coordinates,
  including 672 of 998 coal deposits. Well-formed, in-bounds, correctly transformed — and
  not places. Shipped through two phases; found when a map crop drew coal in the sea.
- **A level-80 drop table published as an ordinary drop.** `DT_PalDropItem` is banded;
  taking `max()` across bands claimed a Chillet drops 30-50 Ancient Relics.
- **A rate-limited eval reported as a 13-point regression.** Every HTTP 429 arrived as a
  decline and was scored as an honest miss. `Decline.transient` already existed and
  nothing read it.
- **Item names published in Japanese.** Two data tables share a filename across
  `L10N/en` and the base path; a single export filename let the base one win.

The pattern in all four: the data was *well-formed and wrong*, and the guard that would
have caught it was either absent or logging at `debug`.
