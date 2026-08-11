# Status

**Read this first, then [`Docs/04-roadmap.md`](Docs/04-roadmap.md) for the detail behind
any line.** This file is the two-minute orientation; the roadmap is the record of how each
number was arrived at.

*Last updated 2026-08-11. `main` is **current** — caught up 2026-08-11 and pushed;
`design-and-phase0` has the same tree. Both are on `origin`.*

---

## Where the project is

| Phase | State |
|---|---|
| 0 — De-risk | **Closed.** A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ · A7 ◐ · A1 ⊘ retired · A5 ✅ accepted at measured behaviour |
| 1 — Q1 resource lookup | **Closed 2026-08-10.** Latency accepted at measured behaviour, carried forward |
| 2 — Q2 Pal spawns + memory | **Closed 2026-08-10** |
| Card artwork + drops | **Shipped.** [ADR-0017](Docs/adr/0017-card-artwork-from-game-assets.md) Accepted |
| 3 — Q3 breeding + Q5 counters | **Order swapped 2026-08-11. Q5 built end to end, unplayed.** Data, candidate set, Tier 2 guard, card, fast path and model path all land; nothing has answered a counter question in real play. **Q3 is blocked** — the ADR-0008 gate needs in-game breeding, not yet unlocked |
| 4 — Q6 tech + Q4 base siting + Q7 corpus | Not started |

## What answers a question today

| Class | Ask | Notes |
|---|---|---|
| Resource location | *"where's the nearest coal"* | + map crop, + "Also drops from" |
| Pal location | *"where can I find Chillet"* | + map crop, + icon, + "Ranch:" |
| Pal drops | *"what does Vanwyrm drop"* | split ordinary / alpha-only / level-banded |
| Item source | *"who drops Flame Organ"* | 151 items, enum-only (not in the lexicon) |
| Boss counters | *"how do I beat Anubis"* | **Tier 2 — computed advice, amber card.** Filtered to Pals you own when the roster has been read; says so plainly when it has not |

Voice in via the local microphone, text in via the channel, cards out to Discord.
One consolidated `answer_query` tool routes all five.

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
3. ~~Phase 3 groundwork~~ — **Q5 is built end to end** (2026-08-11): element matrix, boss
   dataset, owned roster, candidate set, Tier 2 guard, counter card, fast path with
   chained dispatch, and the model path. Q3 is blocked on the ADR-0008 gate, which needs
   breeding unlocked in game.
4. **Play Q5, with capture on.** Nothing has answered a counter question in real play,
   and the 58 new aliases were measured on the same recordings that produced them — real
   play is the independent check for both. Set `[capture] enabled` and `feedback` true;
   clips and a log land in `data/sessions/<timestamp>/`, and the three buttons under each
   card are what promotes a label past `auto`. Press them in the same session, since the
   view does not survive a restart.
5. **Then write the analysis half.** Rephrase-pair detection and failure-run grouping are
   designed and unbuilt, and `harvest_aliases.py` still reads `data/stt_eval/` rather than
   `data/sessions/`. Deliberately in this order: capture is the irreversible part, and the
   analysis can be written any time against clips already collected.

## Decisions waiting on you

| | |
|---|---|
| **Coal coverage** | 552 → 308 clusters. Cave coal is most of Palworld's coal and can no longer be asked for. Accept, or promote the dungeon feature? |
| `maps` and `icons` | One flag pair, two features with very different risk. Separable. |
| Card density | Resource cards gained "Also drops from", Pal cards gained "Ranch:". Editorial. |
| ~~`main` is 101 commits behind~~ | **Resolved 2026-08-11.** It also turned out to have been promoted once already, via PR #1 on GitHub, which no local branch recorded — the "never promoted" line above was wrong from the day it was written. Worth knowing that this file can be confidently wrong about the repo itself, not only about the data. |

## Backlog

- **Find dungeons near me** — spiked, viable, and **thinner than it looked**. The 18
  permanent "Sealed Realm" arenas are already marked on the in-game map; the 13 random
  sites hold a dungeon only ~67% of the time. Does not recover the lost cave coal.
- **Optional branch-keyword prefix** — *"Hey pal, boss help, how do I beat Anubis"*. Raised
  2026-08-11 and parked for review, not rejected: [ADR-0002](Docs/adr/0002-llm-as-router.md)
  already endorses keyword matching **as a fast path with model fallback** while rejecting
  it as the only mechanism, so the shape is pre-authorised provided the keyword stays
  optional. The real prize is not tier disambiguation — that is handled — it is
  **`item_source`**, which cannot be fast-pathed at all today because items are out of the
  lexicon, and which the roadmap calls a structural blocker on the p95 bar. A branch
  keyword names the class without needing entity ranking. Against it: the wake word itself
  mis-transcribes 9.3% of the time (`hippel`, `apal`, `PayPal`), a second required phrase
  compounds that, and longer utterances widen the endpointing window that already produced
  two empty activations. **Pre-flight before building any of it:** record ~30 utterances
  with candidate keywords through `tools/eval/record_stt.py` and score them — a keyword
  that transcribes worse than the wake word adds a failure mode to fix one. Note the
  asymmetry: on the text channel this is nearly free and carries no STT risk at all.
- **Answer both when the tier is ambiguous** — option 3 of the 2026-08-11 tier discussion,
  and the only one of the four not built. When an utterance carries both a counter cue and
  a location cue, emitting *both* cards is never wrong, and there is precedent (a variant
  family already renders two). **Blocked on the tool contract**: `route()` returns one
  `ToolCall` and these are two different tools, so it needs multi-call dispatch rather
  than a cue change. That case abstains to the model today — correct, but it costs a
  model round trip on exactly the phrasings a fast path would most like to claim.
- **The drop fast path's second-entity guard depends on STT** — **measured 2026-08-11, no
  longer a hypothesis.** `routing.py:457` defers a two-Pal drop question to the model only
  when the *second* Pal clears the lexicon floor, so when speech damages that name the
  guard does not fire and the fast path answers a two-answer question from its single
  slot. Reproduced on a recorded transcript: *"what do I get from Astralym and Micora"*
  → `find_pal_drops`, single slot, from a prompt written to provoke it. The earlier
  entry asked for a card count before proposing a fix; that evidence now exists.
  Candidate fix — **a Pal-kind near-miss below the floor should defer rather than
  claim** — but note the 2026-08-11 branch batch says the wider problem is entity
  resolution at 68% accuracy, so tightening this guard treats one symptom of it.
- **Capture gameplay audio as a self-labelling testbed** — **capture and feedback built
  2026-08-11, both off by default; the analysis half is not.** `[capture] enabled` keeps
  the clip and a log line, `[capture] feedback` puts three labelling buttons under each
  card. **Still unbuilt:** rephrase-pair detection, failure-run grouping, and any path
  from `data/sessions/` into `harvest_aliases.py`, which still reads `data/stt_eval/`
  only. So a session collects well and does not yet feed back. The design below is kept
  because it is what the analysis half has to implement. The eval corpus is prompts read aloud from a list, and **read speech is
  hyperarticulated**: the 68% entity accuracy may therefore be *optimistic*, and every
  alias harvested so far comes from the clearest speech this speaker produces. Real play
  is the only source of natural phrasing, game audio bleed, and the truncated utterances
  already seen twice.

  *Cost is nil.* `bot.py` already writes a scratch WAV per utterance because
  faster-whisper reads a file, then deletes it — capturing means **not deleting**. No
  extra write, no added latency, already off the audio thread. 16 kHz mono 16-bit is
  32 KB/s, so a heavy session is ~10 MB. **Keep the audio, not only the transcript**: a
  transcript is re-derivable from audio and audio is not re-derivable from a transcript,
  and every experiment run on 2026-08-11 re-transcribed.

  *Labelling, which is the hard part, mostly costs nothing:*
  - The router's own choice is a free provisional label on every clip — recorded as
    `label: "auto"`, meaning *the system believes this*, never *this is true*.
  - **A rephrase is a free negative label.** A failed query followed within ~60s by a
    similar one that succeeds gives `(bad audio → correct entity)` — exactly an alias
    candidate, with no interaction at all.
  - A **failure run** — several similar attempts, none answered — must emit
    `expected: null` rather than guess. It is worth *more* than a single miss, being
    several pronunciations of one hard name, and one label should cover the group.
    Count the group once so a stubborn query cannot skew the corpus.
  - Human correction via **Discord message components (buttons)**, not reactions: buttons
    ride in the same `send()` payload at zero extra API calls, where six reactions are six
    REST calls and would have to be deferred like `art_post`. Pre-populate only on
    *marginal* cards — declines, near-floor matches, model-path answers the fast path
    abstained on — so the ~80% that are fine stay clean. Card density is already an open
    decision below; this belongs to it.
  - **Intent labels are a correction, not a primary label.** The router already logs the
    class it chose; what is unknown is when that was wrong.
  - **The Discord message id is the join key.** It makes feedback retroactive and precise
    — `/palintel wrong` could only mean "the last utterance", which breaks the moment two
    more questions follow — and it survives the `art_post` edit, since editing does not
    change a snowflake.

  *Two config flags, both default off*, because capture and feedback are separable
  features and STATUS already records the lesson: "`maps` and `icons` are one flag pair
  but two features."

  *Guard against the loop:* labels derived from the router's behaviour are
  self-confirming, so a consistent bug would be quietly ratified by the corpus it
  produces. The human-correction channel is what breaks that, and organic data must carry
  `source: "gameplay"` so it stays measurable apart from the scripted set. Nothing should
  be promoted into `prompts.json` without a human pass — the scripted corpus's whole value
  is that its expectations are known-correct.
- **Harvest STT manglings into lexicon aliases** — the measured next move, and **not** a
  threshold change. The 2026-08-11 branch batch's spoken misses are mostly the lexicon
  finding the right Pal *first* and the router refusing it just under the 0.85 floor:
  Vanwyrm 0.71 from "fan worm", Jetragon 0.82 from "jit dragon", Lamball 0.80 from
  "landball", Mycora 0.83 from "my kora". Sweeping the floor buys 1 hit for 2 wrong
  entities on the 240 and 4 hits for 3, so it stays where it is — a wrong card is the
  trade this project refuses. An **alias is surgical where the floor is global**: it
  raises one true match to 1.0 and loosens nothing else. `score_stt.py` already ends by
  listing the misses as alias candidates, so the first pass costs no recording at all.
  Measure before/after on both sets, and note the aliases are one speaker's manglings.
- **STT accuracy on this speaker's actual speech** — still the widest lever, but read the
  entry above first: raw transcript accuracy is a lower bound, not the pipeline's, and
  `stt.py` records that `large-v3` was *less* accurate than `medium.en` (80% vs 88%) and
  that `initial_prompt` actively hurt. Play on 2026-08-11 produced
  `Vanworm`, `man worm`, `Makora`, `Pantlion`, `Disneyland Ball Drop`
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
