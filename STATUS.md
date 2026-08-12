# Status

**Read this first, then [`Docs/04-roadmap.md`](Docs/04-roadmap.md) for the detail behind
any line.** This file is the two-minute orientation; the roadmap is the record of how each
number was arrived at.

*Last updated 2026-08-12. **Phase 4 landed today and none of it has been played.**
`main` was current as of PR #3 on 2026-08-11; this session's work is not promoted, so
`git log origin/main..HEAD` is the check worth running rather than trusting this line.*

---

## Where the project is

| Phase | State |
|---|---|
| 0 — De-risk | **Closed.** A4 ✅ · A6 ✅ · A2 ✅(caveat) · A3 ◐ · A7 ◐ · A1 ⊘ retired · A5 ✅ accepted at measured behaviour |
| 1 — Q1 resource lookup | **Closed 2026-08-10.** Latency accepted at measured behaviour, carried forward |
| 2 — Q2 Pal spawns + memory | **Closed 2026-08-10** |
| Card artwork + drops | **Shipped.** [ADR-0017](Docs/adr/0017-card-artwork-from-game-assets.md) Accepted |
| 3 — Q5 counters | **Built end to end 2026-08-11, unplayed.** Data, candidate set, Tier 2 guard, card, fast path and model path all land; nothing has answered a counter question in real play. Was "Q3 + Q5"; **split 2026-08-11** |
| **3B — Q3 breeding** | **Unscheduled, and the block moved back inside the repo's reach 2026-08-12.** The save says the Breeding Farm is unlockable **right now** — level 19 met, ForestBoss beaten, 2 of 40 ancient points — and the Egg Incubator is already unlocked. Not a dependency on another player's playthrough; two clicks plus cake production. See below |
| Pal search by attribute | **Shipped, unplayed.** The first new query class since the roadmap. Work-suitability ingest, three-axis filter, card, fast path and model path |
| Mount search | **Shipped and PLAYED 2026-08-11.** Landed 19:13, exercised four minutes later — *"which dragons can I ride at level 60"*, *"which swimming mounts are available"*, *"the fastest ground mount at level 60"*, three of the four on the fast path. **Speed ordering confirmed correct by the player.** The unowned set-difference is still unexercised |
| Tower leaders | **Shipped, unplayed.** *"How do I beat Victor"* resolves to the tower, not the field alpha |
| 4 — Q6 tech + Q4 base siting + Q7 corpus | **Built end to end 2026-08-12, entirely unplayed.** All three classes, three new datasets, every branch swept for theft. Q4 was built **differently from the design** and Q7 without embeddings or synthesis — both deliberate, both recorded in the roadmap. Exit criteria met by construction, not by observation. **Three more classes were added the same day** — base rating (with a resource-narrowed variant), base criteria, and the named technology lookup, taking `PRODUCTION_CLASSES` to 13 — plus per-query spend logging and the session analyser. **650 tests green** |

## What answers a question today

| Class | Ask | Notes |
|---|---|---|
| Resource location | *"where's the nearest coal"* | + map crop, + "Also drops from" |
| Pal location | *"where can I find Chillet"* | + map crop, + icon, + "Ranch:" |
| Pal drops | *"what does Vanwyrm drop"* | split ordinary / alpha-only / level-banded |
| Item source | *"who drops Flame Organ"* | 151 items, enum-only (not in the lexicon) |
| Boss counters | *"how do I beat Anubis"*, *"how do I beat Victor"* | **Tier 2 — computed advice, amber card.** Filtered to Pals you own when the roster has been read; says so plainly when it has not. A tower leader resolves to the **tower**, not the field alpha of the same species |
| Pal search by attribute | *"I need a mining pal"*, *"an electric pal at level 60"* | **Tier 1.** Element × job × wild level, all optional. The only class that takes a description instead of a name |
| Pal info | *"tell me about Shroomer"*, *"what level is Penking"*, *"can I ride Azurobe"* | **Tier 1.** A summary gathered from the datasets already loaded, pointing at the cards that answer each part properly. The rideable line is unconditional, including when the answer is no — silence must not carry it |
| Mount search | *"the fastest mount I can get at level 60"*, *"which mounts don't I have"* | **Tier 1.** Land (= flying **and** ground) or water or either, gated on the **player's** level via the saddle tech. Declines honestly when the roster is unread |
| What to research | *"what should I research next"*, *"what can I unlock at level 30"*, *"what should I spend my ancient points on"* | **Tier 2 — amber.** Set arithmetic over the save's unlocked technologies. Two point pools, never summed. Player level is **inferred as a floor** from what you already have, and the card says so |
| One named technology | *"how do I unlock the breeding farm"*, *"what do I need for the egg incubator"* | **Tier 2 — amber.** The mirror of the row above: you name the technology, it lists **every** gate rather than the first missing one, each ✅ / ❌ / ❔. Already-unlocked answers as a green fact instead of a shopping list. The 588 names are matched **only** against the object of an unlock verb — twelve are ordinary English (`Mine`, `Ranch`, `Mill`) and would wreck the lexicon if they ranked globally — and it costs no schema tokens, unlike the 151-value enum `item_source` pays. A tie defers rather than guessing: five grappling-gun tiers, several cakes |
| Base siting | *"where should I put a base for ore and coal"* | **Tier 2 — amber.** Resource coverage inside a base's own 7.63-unit reach, **on flat ground**, with water distance and the 32 spots the game marks as base camp areas. Still cannot see no-build zones, and says so |
| Base criteria | *"what makes a good base"*, *"what should I look for"* | **Tier 2 — amber, and about no place at all.** What this system checks, each bar with **where the bar came from**, and three things it explicitly cannot check. The only base class that needs no save. The footer says *"my criteria, not the game's"* |
| Base rating | *"how good is my base location"*, *"rate (185, -475)"*, *"is this a good spot for a quartz base"* | **Tier 2 — amber.** The mirror of siting: judges a place rather than searching for one. Three ways to name the place — **a stated coordinate**, your base camps, or where you stand — and a coordinate needs no save. Optionally **narrowed to a resource**, which is the one case where a named entity is the filter rather than a reason to abstain. Four criteria, each pass/fail/**unknown**, and a **count**, never a weighted score. **Three** reference sets: terrain and water against the game's own 32 marked areas, unnamed resources against every node cluster, and a named one against clusters of that resource only. Off-map coordinates decline instead of scoring 0 of 4 |
| How a mechanic works | *"how does sanity work"*, *"what is item rot"*, *"what is mutation"* | **Tier 3 — blue.** The game's own help text **and first-party patch notes**, quoted verbatim with a dated citation. No model in the path. Declines rather than improvising, and does so often |

Voice in via the local microphone, text in via the channel, cards out to Discord.
One consolidated `answer_query` tool routes all of them.

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
| Cost | $0.0048/request on the 2026-08-12 sample — 77% thinking tokens, 14% schema cache. **Now logged per query** to `data/sessions/<session>/costs.jsonl` and totalled on `/palintel status`, evals included |
| Map render | 7.8 ms p50, 25.5 ms p95, ~65 KB, entirely off the graded path |
| Icon coverage | 285 of 286 Paldeck entries |

### The first play session with capture on — 2026-08-11

**41 utterances, 42 clips, 9 human labels.** The first organic, human-corrected data this
project has had, and it earned its keep immediately: it reversed two written decisions,
found a class the product was missing, and caught a failure mode nobody had named.

| | |
|---|---|
| Captured | 41 utterances / 3.7 MB, every one joined to its card by message id |
| Human labels | 9 — 6 `misheard`, 2 `wrong_entity`, 1 `wrong_class` |
| Fast path, before → after | **15/41 → 27/41** on replay |
| Voice p95 | 6.5s against a 2.5s budget ❌, with route p50 **2.83s** — 71% of the total |

What it changed, all of it now regression-tested in `tests/test_session_findings.py`:

- **A tower species answered about the field alpha.** Both `wrong_entity` labels. Seven
  of the nine tower alphas are placed **nowhere in the overworld**, so the reading was a
  fight that cannot be had. A `GYM_` row and its `BOSS_` row share a tribe and therefore
  an element, so the advice was identical either way — only the label was wrong. Now
  prefers the tower. *This reversed a test that asserted the opposite.*
- **`pal_info` did not exist, and was the most-asked shape** — 9 of 41. Seven were
  answered by the **wrong class**: a location card for *"tell me about Shroomer"*, a Tier
  2 counter plan for *"who is Victor"*. A wrong-class answer is worse than a decline
  because it looks like an answer. Built, from data already loaded.
- **The microphone overheard a video.** A clip transcribed as *"Thank you for watching!
  Please like, subscribe, comment and"* — counted as heard, spent 1.8s and a model call,
  and was **captured as a labelled clip**, polluting the corpus capture exists to build.

  **Diagnosed wrong first, corrected by the player.** I read it as Whisper inventing
  subtitle boilerplate on silence, which it genuinely does. It was the kids watching
  YouTube nearby, and Whisper transcribed it accurately. The guard still earns its place
  — video outros are among the most common things a household mic overhears — but it is
  **not** a solution to the general case: any media playing nearby produces fluent,
  plausible text, and a Palworld video would produce Palworld sentences. The real gate is
  the wake word, which fired on this at `threshold = 0.1`. That setting is deliberately
  low because a false negative is a silent failure (ADR-0004); this is a false positive
  from the same choice, and the honest reading is that the trade is working as designed.

  Also a privacy note the config comment already anticipated: capture records whatever is
  near the microphone, including other people in the room. It did.
- **A textbook failure run**, exactly as the capture design predicted: *"Lani"* →
  *"Lening"* → *"Leneen"* in ninety seconds, two declines and a wrong class. Yielded the
  first aliases in this project harvested from unscripted speech rather than read prompts.
- **Cue gaps play found and no amount of desk work would have**: *"which pal's can
  ranch"* (the job trailing the subject, and only the `-ing` form in the vocabulary),
  *"what's Victor's weakness"* (possessive), *"how do I beat Axel & Orserk"* (the game's
  own name for the fight, not in the counterable set).

**And one thing the session made me get wrong before the scorers caught it.** The first
`pal_info` cue set treated *"what's X"* as an info opener. It took Q2 from 43 to 42 with
a wrong card, claims outside the scored classes from 12 to 28, and broke three counter
prompts — swallowing *"what's the nearest memorist"*, four breeding questions and
*"what's strong against Lyleen"*. **A question opener is not an intent.** Reverted to
phrases that name what is being asked for.

### Phase 4, measured 2026-08-12 — all of it deterministic, none of it played

| | |
|---|---|
| Fast-path theft, all three new branches | **0** stolen over the 271 A5 transcripts, swept against the same config with each branch off |
| `score_fast_path.py` across the phase | unchanged — 14/18 Q1, 43/49 Q2, zero wrong |
| Tier 3 retrieval, n=33 | 17 right, 11/11 out-of-corpus declined, **0 wrong**, 5 missed |
| Save join | 117 of 118 unlocked technologies join the table; the one that does not is `PalBox`, which is granted rather than researched |
| Corpus | 3,103 chunks, 394k characters, **entirely from the game's own text** |

**Three things the measurements changed before anything shipped:**

- **The tech tree is not a tree.** 17 of 588 rows have a prerequisite; the gate is
  `LevelCap`. Q6 was reshaped around a level curve with a points budget rather than a
  dependency graph.
- **The Q6 cue claimed two explanatory questions** — *"can you explain technology
  points?"* — and answered a request for an explanation with a shopping list. It now needs
  a recommendation frame as well as the topic. That utterance is the single thing the Q7
  branch claims, and it now comes back with the game's own help page.
- **The Tier 3 scorer read an unanswerable question as fully answered, twice.** *"How do I
  make a sandwich"* scored **1.00** against a Castaway's Journal entry, first because the
  score was unbounded and then, after that was fixed, because unknown query words were
  being filtered out of the denominator. Both are the same bug: a partial match reading as
  a total one.

**And the honest limit of the Tier 3 class:** its five misses are in-corpus questions in
the player's words rather than the game's ("die" against a *Death* entry), and they score
inside the band the unanswerable questions occupy. **No threshold separates them.** That is
a ceiling on lexical retrieval, not a floor to tune, and it is the number embeddings would
have to beat.

### The router picks a CLASS, and nothing had ever measured that

**Measured 2026-08-12 for the first time, at a cost of $0.29.** `score_router.py` has
always scored *entity resolution* — `expected` is a set of names — and six of the twelve
production classes name no entity at all, so on that axis `base_rating`,
`general_knowledge` and an honest decline are the same event. The 88.8% headline is a
number about naming things.

| | |
|---|---|
| Correct entity, `--sample 60` | 89.7% exact, 3.4% wrong — matching the recorded 88.8% / 3.9%, which is what validated the harness |
| **Correct class** | **69.2%** (36 of 52) |
| Over-answered | 13 — answered something the product has no class for |
| Prompts now class-labelled | 930 of 1,031, the rest ambiguous by construction |

**The first thing it found was about the harness, not the router.** Five of the thirteen
over-answers were the model picking `compare_pals` and `get_breeding_combo` — classes
`score_router.py` registers and **the dispatcher does not have**. That is the mistake
`unified_schema`'s own docstring warns about, in the file that warns about it. `--classes`
now defaults to `production` (12) rather than all (15).

**The remaining eight are real and they are one shape:** `pal_info` absorbing any question
that names a Pal and does not fit a narrower class — *"how much stamina does Rinjishi
have"*, *"is loopmoon worth levelling up"*. Whether a summary beats a decline there is a
judgement, and it is now a measured one rather than an impression.

**A third of the corpus asks for something the product cannot do** — 344 prompts about
breeding combos, stamina and whether a Pal is worth levelling. They are labelled
`unsupported`, where **declining is correct and answering is the failure**, which is the
opposite of every other row and was previously invisible.

*Not yet measured:* `base_rating`, `base_criteria` and `pal_search` have prompts (batch
`C##`) and no recordings. They need a session.

### Not measured — and cannot be, without you

| Gap | Why it is stuck |
|---|---|
| ~~`art_post` p95~~ | **Measured**: 531ms p50, 1,157ms p95 over 70 attachments. Edit-in delivery holds. |
| ~~**Do markers land on the actual rock?**~~ | **Closed 2026-08-11.** Ore, stone, wood, paldium walked against the regenerated table — nearest *and* further markers on each card, inside and outside the base — plus quartz at (-53,-960) and (-52,12), ~551 and ~573 units out on different bearings. Near-field and far-field, five resources, separate clusters. |
| **Does `item_source` work?** | All 240 eval recordings predate the class. Ten queries were *asked* on 2026-08-11 and all routed to the model as designed — but **only Chillet's card was read back**, so routing is confirmed and correctness is not. |
| **Does the breeding rank model hold?** | The ADR-0008 gate, and the whole of Phase 3B behind it. **Nothing is left to build**: `build_breeding.py` ingests the ranks, [`Docs/breeding-verification.md`](Docs/breeding-verification.md) is generated, `score_breeding.py` waits to consume it. It needs **eggs hatched in game**, on Steam buildid **`24467282`** with auto-updates off. **The "breeding isn't unlocked" precondition was checked against the save on 2026-08-12 and is wrong** — the Breeding Farm's four stated requirements are all satisfied (level 19 ≤ your floor of 57, ForestBoss beaten, no prerequisite, 2 of your 40 ancient points) and the Egg Incubator is already unlocked. So this is **not** blocked on another player's playthrough, as the failed 2026-08-11 delegation suggested; it is two clicks in the technology menu, then cake production (Ranch + Mill + wheat, eggs, milk, honey) to hatch anything. Note ADR-0008 requires **100% agreement** outside the exception table and refuses partial agreement as a tunable, so one refuted Block 1 row is a decision (the `TableBasedBreedingModel` fallback), not a data point. |
| ~~**Is the owned-Pal roster reaching the cards?**~~ | **No, and it never had. Fixed 2026-08-12.** `owned_species` was built and tested in Phase 3 and never passed into the bot's `PlayerState`, so every counter card in the 2026-08-11 session said *"I haven't read your Pals"* — including the ones the player pressed feedback buttons on. Now polled on the watcher's own five-minute cadence and shown on `/palintel status`; the reference save reads **194 owned characters**. **Every Q5 reading from that session was taken with the roster filter off.** |
| **Is a Q4 base site somewhere you can actually build?** | **Half-closed 2026-08-12, and by the game rather than by play.** Flatness is now measured — the height spread of every placed actor inside the radius, with the bar calibrated as the 75th percentile of the 32 spots the game itself marks `BP_BaseCampPopularArea_C`. What remains unmeasurable is **no-build zones**, and that is now the whole caveat rather than half of it. **Still walk to a suggested coordinate**: the roughness proxy measures the ground where things were *placed*, not the ground everywhere. |
| ~~The Phase 1 latency criterion~~ | **Measured 2026-08-10 and FAILED**: voice p95 4.2s / 2.5s, text 2.0s / 1.5s. Not a tuning problem — p95 sits in the model population whenever a shipped class has no fast path. See the roadmap. |

The first four are in [`Docs/play-session-protocol.md`](Docs/play-session-protocol.md);
three were closed by the 2026-08-10 and 2026-08-11 sessions. **`item_source` is the last
one there, and what it needs now is reading, not asking** — the cards from block 6 either
name the right Pals or they do not, and nobody has looked.

The breeding row is a different shape and deliberately listed alongside them: it is not a
play-session item at all — it needs no save, no bot and no Discord, since breeding
mechanics are global — but it is the same kind of gap, a claim the project cannot check
about itself. It is also the largest one, because a whole phase sits behind it.

One note on how the walk was done: the four nearest nodes sit inside a base whose Pals keep
them mined out, so some were confirmed by position rather than by a deposit being present —
a property of this base's placement, not something the project models against. The further
markers on each card were walked too, outside the base, with deposits standing there.

### Known-uncalibrated

- `min_player_level` / `danger` shipped **uncalibrated** — the rule asks for ~20 nodes of
  known difficulty read in-game and has had none.
- Tree-region coordinates go through a transform fitted only on MainMap landmarks.
- **The Q7 relevance floor is a starting point, not a measurement.** 0.80, chosen at n=33
  on questions written by the person who built the class, against the 50-question in/out
  split the roadmap asks for. It is precision-safe on that set (zero wrong) and its recall
  is the thing real play will contradict.
- **The tower-defeat join is an inference on a key name.** The pak gates a technology on
  `EPalBossType::ForestBoss` and the save records `BOSS_BATTLE_NAME_ForestBoss`; nothing
  states they are the same thing. Five of five flags in the save match a valid enum value,
  which is evidence, not proof. Declared in `tech.json`'s `tower_join_note`.
- **The base radius is read but the circle has never been measured in game.** 3500 world
  units through the fitted transform is 7.63 map units, corroborated only by the cluster
  counts inside your existing bases.
- **A base rating is relative, and n=32 is a small yardstick.** Terrain and water
  percentiles are measured against the game's own marked areas, so each one moves in steps
  of about three points and only 15 of the 32 have a measurable roughness at all. It is a
  calibration taken from the game rather than invented, and it is not a large sample.
- **Base camp positions are parsed out of an undecoded blob.** `BaseCampSaveData` has no
  decoder in 0.24.0, so `saves.base_camps` scans for a unit quaternion followed by an
  in-bounds translation. That is a structural check rather than a fixed offset — a wrong
  window is rejected rather than returned — but it is the same class of parsing as
  `_character_id`, and it found 3 of 3 on one save.
- **Work-suitability levels are unverified against the UI.** `WorkSuitability_*` runs
  1–8 with one Pal at the top of each job. Lamball's 1/1/1 matches the game exactly, so
  the scale is probably the displayed one — but nobody has opened the Paldeck and counted
  the icons on a high-level Pal, and *"Anubis, Mining 6"* is wrong on a card if the game
  shows 4. **A one-glance check settles it**, and until it happens the cards print the
  number and never call it a star count.

---

## Next

**0. Play Phase 4, with capture on. Nothing in it has answered a real question.** Three
classes, three datasets, every exit criterion met by construction. The first session paid
for itself in an hour and reversed two written decisions; this is a larger surface than
that one was.

**[`Docs/test-plan.md`](Docs/test-plan.md) is the full inventory** — every untested class
and every reading that needs retaking, with the exact wording to say, what each item is
testing, and what to expect, all of it produced against your live save. The summary below
is the same list at a glance:

- **Q6.** *"What should I research next"*, *"what can I unlock at level 30"*, *"what
  should I spend my ancient points on"*, *"what should I research for my base"*. Watch for
  the **level floor**: the card says "assuming you're at least level 57". If you are
  higher, it is hiding things you can already research, and how much that matters is a
  question only you can answer.
- **Q4.** *"Where should I put a base for ore and coal"* — and then **walk to the
  coordinate**. Buildability is the one thing this class cannot check and the one thing
  that decides whether it is any use.
- **Q7.** *"How does sanity work"*, *"what is item rot"*, *"explain pal effigies"*. The
  interesting result is not the hits, it is **what it declines** — every decline in your
  own phrasing is evidence about whether lexical retrieval is enough or embeddings are
  needed.
- **Q5, for the first time with a roster.** Every counter card you saw on 2026-08-11 was
  unfiltered, because `owned_species` never reached the pipeline. Ask the same counter
  questions again and see whether the shortlist is now Pals you actually own.
- **One in-game glance:** unlock the Breeding Farm. It costs 2 of your 40 ancient
  technology points and every other requirement is already met. That opens the ADR-0008
  gate, which is the largest single gap in this project.

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
   chained dispatch, and the model path.

   **Q3 left with it 2026-08-11 and is now Phase 3B, unscheduled.** The ADR-0008 gate needs
   a playthrough with breeding unlocked, the second player lined up to run the sheet did
   not have it, and no amount of work in this repo moves that. Splitting it out is not
   giving up on it — it stops a dependency outside the repo from holding a phase open, and
   Phase 4 is free to start without waiting.
4. ~~Play with capture on~~ — **done 2026-08-11, see the session block above.** The
   counter classes, the nine leaders, attribute search AND mount search were all
   exercised - the mount commit landed four minutes before the session started, and the
   player confirmed the speed ordering reads correctly. `pal_info`, "can I ride X" and
   every fix the session produced were built after it and are unplayed. **A second session is now the highest
   -value thing available**, because the first one paid for itself in one hour and the
   fixes it produced are themselves unplayed. Ask especially: `pal_info` phrasings,
   mounts, and whether the tower/alpha preference reads right when you genuinely mean a
   field alpha.

   The original framing, still true of what has not been played:

   **Play, with capture on. This is now the only thing standing between three built
   classes and any evidence they work.** `[capture] enabled` and `feedback` are **true in
   `config.local.toml` as of 2026-08-11** and the write path was smoke-tested end to end
   (clip, log line, message-id join, feedback fold), so the session needs nothing set up —
   it needs playing. Clips and a log land in `data/sessions/<timestamp>/`; the three
   buttons under each card are what promotes a label past `auto`, and they must be pressed
   **in the same session** because the view does not survive a restart.

   Four things are unplayed and each wants different questions asked:

   - **Q5 counters.** Nothing has answered a counter question in real play.
   - **Tower leaders.** *"How do I beat Victor"*, and each of the other eight. The failure
     to watch for is a card about the field **alpha** instead of the tower — same species,
     same name, different fight.
   - **Pal search by attribute.** The four verified questions, plus whatever phrasings
     come naturally. The branch is deliberately narrow; anything it declines that it
     should have claimed is a cue to add, and `/palintel recent` is where that shows.
   - **The 58 aliases**, measured on the same recordings that produced them. Real play is
     the independent check.

   **One in-game glance, unrelated to the bot**: open the Paldeck for a high-level Pal —
   Anubis or Blazamut — and count the Mining icons. That settles the work-level scale
   question in Known-uncalibrated above.
5. **Then write the analysis half.** Rephrase-pair detection and failure-run grouping are
   designed and unbuilt, and `harvest_aliases.py` still reads `data/stt_eval/` rather than
   `data/sessions/`. Deliberately in this order: capture is the irreversible part, and the
   analysis can be written any time against clips already collected.
6. ~~**Re-measure the router when an eval run is next worth its cost.**~~ — **done in
   part, 2026-08-12.** A `--sample 60` run validated the harness and added the class axis
   above; the entity numbers held. What would justify the full ~$1.40 run is now written
   down as a rule in `score_router.py`'s header rather than left to judgement: a
   wrong-class rate above ~10%, or any class under 50% on n≥10. **`unsupported` at 7% is
   the one that qualifies**, and it is a decline-policy question rather than a routing bug.

   The original note, still true of what it described:

   **Re-measure the router when an eval run is next worth its cost.** `pal_search` joined
   `PRODUCTION_CLASSES`, so the model now picks between six classes rather than five and
   the schema carries two small enums it did not. The deterministic scorers show no
   regression — `score_fast_path.py` is unchanged at 14/18 Q1, 43/49 Q2, zero wrong, and
   `score_branches.py` at 16/16 written — but those do not exercise the model path.
   Nothing about this is urgent: no eval prompt names an attribute query, so a run today
   would measure the schema change against a corpus that cannot see it.

## Decisions waiting on you

| | |
|---|---|
| **Q7: does a robots.txt naming ClaudeBot settle it?** | [`Docs/corpus-sources.md`](Docs/corpus-sources.md) registers 16 community sources for the strategy/consensus layer the pak cannot supply. **Two of the three best carry a CC licence that permits reuse and a robots.txt that names ClaudeBot and forbids it** — `palworld.wiki.gg` (CC BY-SA 4.0) and `palworldgame.wiki`, with Game8 blocking GPTBot. Licence and stated wishes point opposite ways and only you can pick. Answering this once removes or restores the three highest-coverage sources; nothing else about a community corpus can be sequenced first. Nothing has been ingested. |
| **Q7: embeddings, or leave it lexical?** | The lexical baseline answers questions asked in the game's own words and cannot answer paraphrases — measured, and the two bands overlap so no threshold separates them. Embeddings would fix that and cost a new local dependency (sentence-transformers, ~2GB, but the GPU is already there for STT) or a network call per query, which ADR-0003 argues against. **Play first**: the misses in your phrasing are the evidence, and the baseline exists so the comparison is a measurement rather than an assumption. |
| **Q7: should a decline fall through to the corpus?** | Not built, deliberately. `general_knowledge` is a class the router may *choose*, not a catch-all. The roadmap calls the fallback "the change that makes the system a chatbot", and it is the largest change to this project's risk posture available — worth your explicit yes rather than my inference. |
| **Q7: synthesis, or keep quoting?** | Today a Tier 3 card quotes the game verbatim, so no model touches the text and ADR-0011's drift failure cannot occur. Synthesis earns its place only when an answer needs two chunks combined; nothing has shown that yet. |
| **Q4: is the computed version enough?** | The roadmap's Q4 was twenty curated sites with prose rationale; what shipped is "what falls inside a base's radius", because the curated version needed invented flatness scores and community prose. If you want the curated half, it needs a source you trust and a way to verify it. |
| **Should `pal_info` answer questions it cannot answer?** | Measured 2026-08-12: it absorbs *"how much stamina does Rinjishi have"* and *"is loopmoon worth levelling up"* — questions with no class, where a summary is arguably better than a decline and arguably the wrong-class failure the first play session named. The decline policy was rebalanced on 2026-08-11 toward answering, on the finding that declining an answerable query is also a failure; this is the same trade seen from the other side. **Your call, and the first play session is where it will feel wrong or fine.** |
| **Set `cost.balance_usd`** | Spend is now logged per query and totalled, but the balance is 0 so nothing is deducted. Put what you actually loaded onto the key in `config.local.toml` and `/palintel status` will warn before a depleted balance turns into a wall of declines. |
| **Coal coverage** | 552 → 308 clusters. Cave coal is most of Palworld's coal and can no longer be asked for. Accept, or promote the dungeon feature? |
| `maps` and `icons` | One flag pair, two features with very different risk. Separable. |
| Card density | Resource cards gained "Also drops from", Pal cards gained "Ranch:". Editorial. |
| ~~`main` is 101 commits behind~~ | **Resolved 2026-08-11, and again the same day via PR #3** — `origin/main..HEAD` is 0. It also turned out to have been promoted once already, via PR #1 on GitHub, which no local branch recorded — the "never promoted" line above was wrong from the day it was written. **And the local `main` ref was stale by 37 commits after PR #3**, so `git branch -vv` said "behind" about a branch that was in fact ahead of everything local; fast-forwarded. Worth knowing that this file can be confidently wrong about the repo itself, not only about the data — and that the local ref is a cache, not the answer. Compare against `origin/main`. |

## Backlog

- **"What changed in the latest patch"** — asked of the corpus on 2026-08-12 and it
  **declined at 0.48**, correctly. Retrieval has no time axis: 156 patch chunks all match
  "patch" equally and nothing in a lexical score knows which is newest. But
  `patch_notes.json` carries a version and a date on every entry, so this is a **lookup,
  not a search** — a deterministic "newest note, its sections" answer with no retrieval in
  it at all. Not built because it is a new class and a new card on a day that already
  added three, and because it is the kind of thing play should confirm people ask before
  it earns one. *"What is mutation" already works*, which is the harder half.
- **Raid safety, the fourth base-siting lever** — the community names flat terrain,
  resource density, raid safety and water access. Three are now computed;
  `BP_RaidBossAreaBaseCampPoint_C` exists at 16 placements and is **not obviously the same
  concept**, and nothing extracted would let a card claim a site is safe from raids. Left
  alone rather than approximated. The 1,295-class survey is where a better signal would be
  found if one exists — `PalLimitVolumeBoxComponent` (360) and `BP_PalNoClimbVolume_C`
  (311) are the two worth dumping first.
- ~~**Resolve a boss by its human name**~~ — **shipped 2026-08-11**, and it corrected the
  correction. The previous entry said `DT_UniqueNPCText`'s `_LEADER` / `_LEADER_PAL` pairs
  link a human to a tower by an **inference** on the key suffix, strong at 8 pairs. That
  was still short. **`pal_names_flat.json` states each pair outright** —
  `PAL_NAME_SnowBoss` is `"Victor & Shadowbeak"`, one string — in a file this project
  already extracts and already builds the lexicon from, and it carries a **ninth** pair,
  Zenara & Astralym, which the text table has no key for. So the note that Astralym's
  tower simply has no leader was an artefact of reading one table.

  The two sources agree on all eight they share and `build_bosses.py` fails if they stop.
  What is *still* derived is one step, not two: reaching `GYM_BlackGriffon` from the name
  "Shadowbeak" goes through the `BOSS_`-prefix inference already declared project-wide.

  **The trap worth remembering** — a leader must resolve to a **character id**, never to
  the Pal's name. `bosses.json` sorts by `(kind, character_id)`, so a name index reaches
  `BOSS_BlackGriffon`, the field **alpha**, before `GYM_BlackGriffon`. Both are called
  Shadowbeak, they are different fights, and a card naming the wrong one would look
  entirely correct.

  Leaders are a **third lexicon kind**, not aliases of the tower Pal: aliasing would
  collapse Victor into Shadowbeak during ranking and lose exactly that distinction. The
  eight names were measured against the 271 A5 transcripts before being added — the
  highest score any reaches against an unrelated fragment is 0.667, under both floors.

  Zenara's tower resolves and then **declines**: every `WorldTreeDragon` row carries
  `ElementType::None`, so Astralym cannot be countered by type. That is the pak's answer
  and nothing invented an element to fill it.

  **Still absent:** tower ordinals (nothing says Victor's is the 5th), faction names like
  "PAL Genetic Research Unit", and tower boss levels.
- ~~**Pal search by attribute**~~ — **shipped 2026-08-11.** The first genuinely new query
  class since the roadmap was written, and a gap in the class inventory rather than a bug:
  every other class takes a NAMED entity and returns facts about it, while this one takes
  a description and asks which Pals match. Q1-Q7 are all "I know what I want, tell me
  about it" and never "tell me what I want."

  **Four real questions, verified declining that morning and answering that afternoon**,
  kept verbatim because they were asked rather than invented: *"give me an electric pal
  that is level 60"*, *"what electric pals are around level 60"*, *"I need a new mining
  pal"*, *"what pal is best at mining"*. All four are parametrised tests.

  Element × job × wild level, all optional, at least one required. Tier 1 and green: it
  selects rows and orders them by an integer the game states, which is the same kind of
  claim as a coordinate — unlike the counter card next door, which computes a
  recommendation and is amber for it.

  **The guard is the absence of a named entity**, which is what makes it structurally
  unable to steal from another class: every query that names something belongs to one of
  those. `score_fast_path.py` is unchanged across the change — 14/18 Q1, 43/49 Q2, zero
  wrong — and that is the measurement, not the reasoning.

  **Two things the data did that the design did not expect:**

  - **A level band is a set, not a range.** Grizzbolt is placed at (18, 22) and (70, 72)
    and (80, 80). Taking min and max gives "lvl 18-80" — arithmetically true, reads as
    continuous, and would answer *"an electric Pal at 45"* with a species that appears at
    no such level anywhere. Well-formed and wrong, caught before shipping only because
    the number looked odd. The bands are kept distinct.
  - **There is no electric Pal at exactly level 60.** Feybreak places most species at 80,
    so wild levels are lumpy and exact containment often matches nothing. The card widens
    to the nearest bands and **says so on the first line**, because "the closest thing to
    an electric Pal at 60" and "an electric Pal at 60" are different claims.

  Work suitability is a new ingest (`build_work.py`), thirteen jobs, with the labels taken
  from the game's own UI strings so a card says "Kindling" and not `EmitFlame`. Nothing in
  it is derived — but see Known-uncalibrated for the one thing about it nobody has
  checked.

  **Every one of them was saved by the 0.85 floor, and the near-misses got closer each
  time.** Rayhound 0.71 on "electric pals", Carnibora 0.71 on "beat the", and worst,
  **Anubis at 0.77** on *"I need a new mining pal"* — Anubis being the game's best mining
  Pal, so a location card for it would have read as very nearly a correct answer for
  entirely the wrong reason. That is the strongest evidence yet for the alias work's
  decision to fix entity resolution surgically instead of lowering the floor.

  **Decided 2026-08-11: "level" always means the PAL's level, never the player's.** One
  meaning, no preposition parsing, nothing derived. The deciding fact is that the cards
  already speak this way - a spawn card prints *"Anubis, lvl 68-72"*, which is the Pal -
  so a query where "level 60" meant the player would make one word mean two things on the
  same card. It also stays a **fact rather than a judgement**: Pal level comes straight
  from `pal_spawns.json` level bands, while filtering by player level needs a "how far
  above your level can you cope" constant, and this project already has one uncalibrated
  difficulty rule it has not paid off (`min_player_level` / `danger`, below). Pal level is
  also the primitive - *"what can I use at 60"* is expressible as *"Pals <= 60"*, while
  the reverse makes the exact question unaskable.

  *What that gives up, and it is real:* players do think in their own level, so
  *"an electric Pal for level 60"* often means "something I can use", and this answers it
  slightly obliquely. Recoverable later as a Tier 2 layer on top, once player level is
  actually readable - it is permanently `None` today, living in the `Level.sav` blob
  behind the stale decoders - and once the headroom constant has been calibrated from
  play rather than guessed. Baking it in now would ship an uncalibrated judgement as a
  fact.

  *Note the product is already inconsistent here*: resource cards print `lvl 28+`, which
  is a PLAYER requirement, from that same uncalibrated rule. So "level" does not have one
  meaning today either, and this decision only settles the new class.

  **AMENDED 2026-08-11, same day, by the mount questions.** *"What is the fastest flying
  mount I can get at level 60"* means the **player's** level: a saddle is a technology
  with a `LevelCap`. The amendment is narrow and the decision's own reasoning permits it —
  player level was rejected because filtering by it needed an uncalibrated "how far above
  your level can you cope" constant, and **a saddle gate needs none, because the game
  states the number.** So: *level means the Pal's, except where the game itself states a
  player gate.* The two live in separate fields (`level` and `player_level`) and separate
  schema slots so neither can be mistaken for the other, and the card spells out "player
  level 60" where every other card prints the Pal's.

  Note this does not recover the general case the decision gave up: *"an electric Pal for
  level 60"* meaning "something I can use" still needs the headroom constant, and player
  level is still permanently `None` from the save. The mount case works because the
  number comes from the utterance and the gate comes from a table.

  *Sorting: highest Pal level first*, with the caveat the counter card just had to learn -
  highest is a proxy for strongest and nothing more, so the card must not imply a ranking
  the data does not carry. **Shipped as "sorted by highest level, which is not a ranking"
  in the footer**, and only when no job was named: with a job, the sort key is that job's
  level, which is a number the game states and can be presented as one.
- **Mount type: flying vs ground** — **the one thing the pak does not say, and it may not
  matter.** Asked for on 2026-08-11 as three separate questions ("fastest flying /
  swimming / ground mount at level 60"). Swimming shipped; flying and ground shipped
  **merged into one "land" category**, because the game merges them:

  *No flight flag.* Seven signals measured against a hand-labelled set and all falsified —
  `SwimSpeed == RunSpeed` (10/19 flyers), `GenusCategory == Bird`,
  `PalFlyMeshHeightCtrlComponent` (2/6), the `Pawn_NoDamageFlyPal` collision profile
  (2/6), `RidePositionType` (it is seat position — BackRide, BiggerHorseRide — not
  flight), fly-named animation assets (precision 6/6, recall 6/12), and decisively **the
  set of component classes present in every labelled flyer and no labelled ground Pal is
  empty**. All 532 data tables in the pak were listed; none concerns movement. The flag
  is in the native parent class or in graph data CUE4Parse's export does not surface.

  *And no flight speed either*, which is why the merge is honest rather than a
  workaround: a flyer's ridden speed is `RideSprintSpeed`, the same column a ground mount
  uses. Splitting them would produce the same ranking twice with an invented label on
  each. The card says "flying and ground mounts share one speed in the game files".

  **What separating them would actually buy** is a filter, not a better ranking — "only
  show me things that fly". Worth doing only if play shows players asking for that
  specifically, and it needs the inherited-component tree or BP bytecode, which is the
  same wall the ranch spike hit.
- **Refine a result set with a follow-up** — *"which pals can ranch?"* then *"which ones
  are for level 60?"*. **Designed, deliberately NOT built 2026-08-11.** One observed
  instance is not enough to justify the mechanism, and the decision is to wait until play
  shows whether refinement follow-ups are a habit. Revisit if they become common or
  annoying.

  Kept because the analysis is the expensive part and it should not be redone:

  *It is a different mechanism from ADR-0013, not an extension of it.* Entity inheritance
  carries one **name** forward — *"what about the alpha"*. This carries a **filter set**
  forward and merges a new filter into it. Attribute search stores nothing today, on
  purpose: "conversation memory holds one referent per turn and this class produces five."

  **The trap, and it is live.** `"ones"` scores **0.80 against stone**, over the 0.78
  resource floor. `_FOLLOWUP` does not currently match *"which ones are…"*, so the query
  declines. The moment that phrase becomes a follow-up trigger, `_inherit` sees
  `_subject()` return stone, concludes the utterance names its own entity, and emits a
  **stone locations card**. A deferral becomes a confidently wrong answer. Fix that
  first, not after.

  Three more that would need settling: a turn would hold an entity **or** a filter set
  but never both, or *"what about the alpha"* after an attribute query has five referents
  again; the merge inherits the prior class's meaning of "level" (the Pal's after a ranch
  query, the player's after a mount one), so the card must state which it used; and
  accumulated filters must be **printed on the card**, because two refinements deep
  nobody can see what set they are looking at. `_filters_line` already exists for the
  empty case.

  If built: one refinement only, no chaining, expires with the turn.
- ~~**A bare element plural as the subject**~~ — *"show me level 60 dragons"*. **Built and
  reverted the same day, 2026-08-11.** The measurement was clean (fires on 2 of 281
  transcripts, both genuine) but making it work needed an allowlist of exactly one
  element, because `plants`, `grounds` and `flames` are ordinary English about other
  things and *"which plants can I grow"* is not a request for a Grass roster. **A rule
  with one hand-picked exception is a special case wearing a rule's clothes.**

  Reverting it found a better bug: *"which dragons can I ride at level 60"* was being
  **answered without the element filter** — every mount at level 60, under a card titled
  "Mounts", with a stated filter silently dropped on the fast path. The general rule that
  replaced the exception is *an element word we cannot attach means defer*, which covers
  the case honestly and is the same principle as the drop branch's second-entity guard.
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
- **The drop fast path answers a two-Pal question from one slot** — **reproduced in real
  play 2026-08-11, and then ACCEPTED.** The guard defers only when the *second* Pal
  clears the lexicon floor, so damaged speech lets the fast path answer about the first
  Pal alone: *"what does Gidra and Dromatide drop?"* → `find_pal_drops(Gildra)` in 0.1s,
  with Dromatide's best match at 0.74, under the floor.

  **Left as is on the player's call**, and the reasoning is a usage fact this project had
  no other way to learn: *"I don't think I will ask multiple pal questions — it's hard
  enough to say one Pal's name!"* Two-Pal questions are rare because the speech is hard,
  and answering the first of two is a partial answer rather than a wrong one.

  Revisit if it becomes troublesome in play. The candidate fix is unchanged — **a
  Pal-kind near-miss below the floor should defer rather than claim** — but it must be
  swept on the 271 transcripts first, because near-misses are common and it would cost
  drop coverage across the board to fix a case that may never be asked. Note also that
  the branch batch calls the wider problem entity resolution at 68%, so this guard treats
  one symptom of it.
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

**And a fifth, from the same day, which is the same lesson in a third file.** The cell
scan filters to three actor prefixes, so `placement_class_counts.json` is a census of what
we already collect — and it had been read as a census of what is *in the world*. A `survey`
mode with no filter at all found **1,295 actor classes and 1.9 million actors**, including
`BP_BaseCampPopularArea_C` (the game marking 32 base sites), `BP_SimpleWater_C` (1,257
water bodies) and the fishing spots. None of it was hidden; nothing had asked. That is the
third time — 81 of 532 data tables, one key in one boss table, and now three prefixes of
1,295 classes. **"I searched for it" is only as strong as the term searched for**, and in
this project the term is usually a filter somebody wrote for a different purpose.

Two more from 2026-08-12, both caught by reading output rather than by a passing test:

- **26 technology names were published as raw markup.** The name table stores pointers —
  `<mapObjectName id=|BreedFarm|/>` — and some rows spell the tag `mapObjectname`. A
  case-sensitive pattern read those as plain text and shipped the tag as the name. Third
  casing trap in this project after `Boss_Anubis` and `SkillUnlock_Thunderdog_Ice`, and
  the first on a tag rather than an id: **the pak's casing is not to be trusted on any
  join at all.**
- **The Tier 3 scorer gave an unanswerable question 1.00, twice, for two different
  reasons.** *"How do I make a sandwich"* matched a Castaway's Journal entry perfectly —
  first because the score was unbounded and one title word could exceed 1.0, then, after
  that was fixed, because query words the corpus has never seen were being dropped from
  the denominator, leaving "make" as the entire question. Both are one bug in two costumes:
  **a partial match presented as a total one.**

Two more, from 2026-08-11, that are a different class — nothing was wrong, something was
simply **not connected**, and everything downstream reported success:

- **The Q5 counter fast path was dark in production for a day.** `StubRouter` grew the
  branch, `score_branches.py` measured it at 16/16 on the written prompts it can claim,
  the tests passed, and `build_router` never passed `counters=True`. So every counter
  question in play paid a model round trip for an answer the stub already had, and this
  file said "fast path with chained dispatch" lands. No commit or ADR argues for leaving
  it off; it was an omission. **A measurement of a component is not a measurement of the
  system** — `score_branches.py` constructs its own router and therefore could not have
  noticed.
- **A table was searched for one key and declared to lack the answer.** Twice. The
  leader mapping was recorded as absent (wrong table), then as an inference (right table,
  but not the one that states it outright). `pal_names_flat.json` had `"Victor &
  Shadowbeak"` in it the whole time, in a file this project already reads on every
  lexicon build. Searching all 81 tables for `BOSSNAME_DEMO_*_LEADER` found nothing new,
  because the second source does not use that word — **"I searched for it" is only as
  strong as the term searched for.**
- **The owned-Pal roster never reached a card.** Found 2026-08-12. `saves.owned_species`
  was built in Phase 3, unit tested, and never passed into the bot's `PlayerState`, so
  every counter answer in the 2026-08-11 session was unfiltered while the card politely
  explained that it had not looked. The same shape as the fast path above, and worse in
  one way: the caveat it printed was *true*, so nothing about the output looked wrong.
