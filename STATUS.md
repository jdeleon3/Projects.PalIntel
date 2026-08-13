# Status

**Read this first, then [`Docs/04-roadmap.md`](Docs/04-roadmap.md) for the detail behind
any line.** This file is the two-minute orientation; the roadmap is the record of how each
number was arrived at.

*Last updated 2026-08-12, after the second play session. **Phase 4 has now been played,
and it produced the first card that got the player killed.** `main` was current as of
PR #3 on 2026-08-11; this session's work is not promoted, so `git log origin/main..HEAD`
is the check worth running rather than trusting this line.*

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
| **Party voice (Discord receive)** | **Restored and PLAYED 2026-08-13**, after months recorded as blocked. 13 spoken questions answered end to end from a voice channel, attributed to the speaking member rather than a configured name — [ADR-0012](Docs/adr/0012-dual-input-channels.md) restored, not replaced. **The blockage was never DAVE**: it decrypts 99.8% of packets, and py-cord 2.8's receive package was the fault. 13 defects fixed in [`PyDiscordDave`](../PyDiscordDave/README.md), two of them this repo's. `mic.py` stays the default and the fallback. Recall still unmeasured — see the backlog entry |
| Tower leaders | **Shipped, unplayed.** *"How do I beat Victor"* resolves to the tower, not the field alpha |
| 4 — Q6 tech + Q4 base siting + Q7 corpus | **Built 2026-08-12 and played the same day** — see the session block below. All three classes answered real questions; the session found three defects in older code and two in Phase 4's own. The original note, still true of how it was built: **Built end to end 2026-08-12, entirely unplayed.** All three classes, three new datasets, every branch swept for theft. Q4 was built **differently from the design** and Q7 without embeddings or synthesis — both deliberate, both recorded in the roadmap. Exit criteria met by construction, not by observation. **Three more classes were added the same day** — base rating (with a resource-narrowed variant), base criteria, and the named technology lookup, taking `PRODUCTION_CLASSES` to 13 — plus per-query spend logging and the session analyser. **650 tests green** |

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

### The second play session — 2026-08-12, and the first one with a consequence

**52 utterances, 57 minutes, 6 human labels, 32 of 49 answers on the fast path.** The first
play of Phase 4. Full detail in [the roadmap](Docs/04-roadmap.md#the-second-play-session--the-first-one-that-could-kill-you-2026-08-12);
the short version is that it corrected what this project believed about **its own
measurements**, and that the two most important findings came from the player and not from
the logs.

| Found in the logs alone | Found only because the player said so |
|---|---|
| The spend ledger was wrong by 3.8× | *"I walked to those coordinates and died"* |
| No spoken coordinate could be parsed | *"three locations in the same place"* |
| A stated Q6 filter was silently dropped | *"I was standing in base 3 and got bases 1 and 2"* |
| | *"there are nodes you put a crude oil extractor on"* |

**Fixed this session:**

- **Spend was reported as $0.3344 over 56 queries, 55/56 reaching the model. It was
  $0.0880 and 16/56.** `FastPathRouter` forwarded `last_usage` to the model backend, which
  never clears it, so every fast-path answer after the first model call was charged the
  previous call's cost. Both figures the ledger exists to produce, wrong in the direction
  that drains a prepaid balance early — and this landed the day after `balance_usd` was put
  in front of you as a decision. **Set it now; it is safe to.**
- **A second bug underneath it**: the bot decided fast-vs-model by testing whether the word
  `cue` appeared in a branch's rationale prose. `_tech_named_call` does not write it, so all
  five technology lookups were answered by the stub in milliseconds and logged as model
  calls in both the capture log and the ledger. Path now comes from whether a call happened.
- **A spawn card sent the player to a level 68–72 area they could not survive.** The level
  55 field alpha was 831 units away, in the dataset, and was **nearest of all 26 Anubis
  areas and densest of all 26** — `find_pal_spawns` falls through to the first kind with any
  rows, so 25 ordinary areas hid it before the sort ran. The card now carries a
  `Field alpha:` row; nothing was reordered.
- **A `Nearest:` row**, for the complaint that three markers were "in the same place". They
  were: density is spatially clustered, so the top three by density sat in one habitat 818
  units away — while the player was **standing in** a Lovander area 8 units away. Distance
  entered the sort only as a tiebreak. The Phase 2 finding that distance-first ranking is
  wrong still stands and is untouched; the card gains a row and a footer that says
  *"numbered by likelihood, not by distance"*.
- **No spoken negative coordinate could be parsed** — Whisper writes a spoken minus as the
  *word* — so the feature was unreachable by voice on a map that is negative nearly
  everywhere, and a failed parse fell through to rating **the player's own position** under
  the title "Where you're standing". The off-map refusal shipped four commits earlier could
  never fire, because refusing a coordinate needs reading one. An announced pair that will
  not parse now defers instead of substituting. 0 of the 271 A5 transcripts are claimed by
  the new decline.
- **"Rate my base" now starts with the base you are standing in.** `MAX_CARDS` is 2 and
  the order was the save's, so a player **0.5 units inside base 3** was shown bases 1 and
  2 — 1,046 and 1,113 units away — and told "1 more base not shown". Every number needed
  to choose correctly was already on that card.
- **Crude oil has 185 locations, and the card said it had none.** It published *"crude oil
  isn't a mineable node — it comes from oil rigs, so there are no map locations to give
  you"*. `BP_LevelObject_OilField_C` is placed across the island, its blueprint states
  `ProvidableStaticItemId: CrudeOil`, and the game's own item text — in a table this
  project already ingests — says *"Obtained by installing a Crude Oil Extractor in an oil
  field."* The extraction that "found no spawner class" reads `BP_PalMapObjectSpawner*`.
  **An absence in a filtered search became a claim about the world**, then propagated into
  two design documents, the node dataset's `known_gaps`, a card and four tests. Fourth
  instance of that pattern here, and the first published to the player as prose. Fixed by
  widening the search — all 30 `BP_LevelObject_*` blueprints are now asked whether they
  provide an item, and *which ones do is a fact about the pak rather than a list somebody
  typed*. **This also makes *"where should I build my base for crude oil"* work**, which
  was asked in this session and answered with a bare node card.
- **The feedback channel now asks instead of diagnosing.** A fourth button, first in the
  row — `📝 Not what I expected` — opens a modal with one free-text field, and
  **`/palintel wrong` as a reply to any card** covers the case the buttons cannot: a card
  that only turns out to be wrong after you travel. Two of the six labels pressed this
  session were `wrong_class` for things that were not a wrong class, because the taxonomy is
  a router's vocabulary. Notes are printed **above** every inference the analyser makes.

**Found and deliberately not fixed** — each wants its own sweep rather than a ride-along:

- **Q6 drops a narrowing it cannot map.** *"What tech should I research for my mining pals"*
  returned the unnarrowed list led by Advanced Arrow; *"what weapon"* narrows correctly. The
  general rule it wants is *a narrowing we cannot map means defer* — the rule `_base_call`'s
  `weak` flag already implements one class over.
- **`item_source` claims more than it holds.** *"Where can I find cakes"* → *"Cake comes
  from — Lovander | 1 | 1%"*, true and useless: cake is crafted, and the player needed it
  for breeding. High Quality Pal Oil returned 41 correct sources. The class is a **drop
  table** and the card title says "comes from". A title and a footer, not a dataset.
- **Q7 retrieval picks near-duplicate chunks.** *"How do I assign a pal to the breeding
  farm"* returned the Breeding Farm help guide (0.90) and, as its second quote, the Breeding
  Farm *structure* text — two chunks restating the goal, when the corpus's `Base` chunk
  holds the actual mechanism ("interacting with the Palbox allows you to summon Pals to your
  base"). **The card already renders a second quote**, so this is diversity in retrieval and
  not the synthesis question below.
- **`data/all_boss_landmarks.csv` is ingested by nothing.** 159 boss placements with world
  coordinates **and stated levels**, in `data/`, while `bosses.json` carries `"level": null`
  on every entry. Fourth instance of this project's recurring pattern: the data was there
  and nothing asked.

**One thing the session lost.** `activity.py` keeps latency in a one-hour in-memory window
and writes nothing, so the **voice p95 of 6.2s against the 2.5s budget** exists only in a
status line pasted into a chat log. Costs persist; latency does not. Worth fixing before
the next session.

**What did not move:** `score_fast_path.py` unchanged at 14/18 Q1, 43/49 Q2, zero wrong;
`score_branches.py` 16/16 written; **671 tests green**. The session yielded **zero aliases**
— both rephrase proposals came back `NO SURFACE FORM`, and one (`'grappling' → Anubis`) was
a false pair between two unrelated questions.

### Phase 4, measured 2026-08-12 — all of it deterministic, none of it played

| | |
|---|---|
| Fast-path theft, all **six** new branches | **0** stolen over the 271 A5 transcripts, re-swept on the shipping config after the last one landed. Note what this does *not* say: the A5 set predates these classes, so nothing in it asks about a base site or names a technology. It proves they do not steal, not that they fire |
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
always scored *entity resolution* — `expected` is a set of names — and six of the
then-twelve production classes name no entity at all, so on that axis `base_rating`,
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
now defaults to `production` — 12 at the time of the run, 13 today — rather than all (15).

**The remaining eight are real and they are one shape:** `pal_info` absorbing any question
that names a Pal and does not fit a narrower class — *"how much stamina does Rinjishi
have"*, *"is loopmoon worth levelling up"*. Whether a summary beats a decline there is a
judgement, and it is now a measured one rather than an impression.

**A third of the corpus asks for something the product cannot do** — 344 prompts about
breeding combos, stamina and whether a Pal is worth levelling. They are labelled
`unsupported`, where **declining is correct and answering is the failure**, which is the
opposite of every other row and was previously invisible.

*Not yet measured:* `base_rating`, `base_criteria` and `pal_search` have prompts (batch
`C##`) and no recordings. They need a session. **`technology_lookup` has neither** — it
landed after this run, taking production to **13** classes, so `--classes production` now
offers a class the corpus has no prompts for. Next class batch should cover it; the
deterministic sweep over the 271 A5 transcripts is all it has today.

### Not measured — and cannot be, without you

| Gap | Why it is stuck |
|---|---|
| ~~`art_post` p95~~ | **Measured**: 531ms p50, 1,157ms p95 over 70 attachments. Edit-in delivery holds. |
| ~~**Do markers land on the actual rock?**~~ | **Closed 2026-08-11.** Ore, stone, wood, paldium walked against the regenerated table — nearest *and* further markers on each card, inside and outside the base — plus quartz at (-53,-960) and (-52,12), ~551 and ~573 units out on different bearings. Near-field and far-field, five resources, separate clusters. |
| ~~**Does `item_source` work?**~~ | **Answered 2026-08-12, and the answer is "for drops".** *"Where do I get a high quality pal oil"* returned 41 sources led by Mammorest at 100%, 5–10 — correct and useful. *"Where can I find cakes"* returned **Lovander at 1%**, which is true and is not the answer: cake is crafted, and the player asked because breeding needs it. `by_item` is a **drop table** and the card says *"Cake comes from"*. Open as a card-wording item above, not as a measurement gap. |
| **Does the breeding rank model hold?** | The ADR-0008 gate, and the whole of Phase 3B behind it. **Nothing is left to build**: `build_breeding.py` ingests the ranks, [`Docs/breeding-verification.md`](Docs/breeding-verification.md) is generated, `score_breeding.py` waits to consume it. It needs **eggs hatched in game**, on Steam buildid **`24467282`** with auto-updates off. **The "breeding isn't unlocked" precondition was checked against the save on 2026-08-12 and is wrong** — the Breeding Farm's four stated requirements are all satisfied (level 19 ≤ your floor of 57, ForestBoss beaten, no prerequisite, 2 of your 40 ancient points) and the Egg Incubator is already unlocked. So this is **not** blocked on another player's playthrough, as the failed 2026-08-11 delegation suggested; it is two clicks in the technology menu, then cake production (Ranch + Mill + wheat, eggs, milk, honey) to hatch anything. Note ADR-0008 requires **100% agreement** outside the exception table and refuses partial agreement as a tunable, so one refuted Block 1 row is a decision (the `TableBasedBreedingModel` fallback), not a data point. |
| ~~**Is the owned-Pal roster reaching the cards?**~~ | **Confirmed in play 2026-08-12** — every counter card in the session printed *"checked 143 of your Pals"*, and the tower/alpha split read correctly on all three asked (Grizzbolt → Zoe's tower, Victor → Victor's tower, Anubis → field alpha). **One thing that count hides:** you own 195 species and 50 of them are `BOSS_`-prefixed, which `counters.py` excludes. 35 duplicate a base species you also own, but **14 are species you hold ONLY as an alpha** — `boss_suzaku`, `boss_volcanicmonster`, `boss_winggolem` among them, all typed — and they can never appear in a shortlist. Whether a caught alpha should count as a party member is a judgement; *"checked 143 of your Pals"* reading as your whole roster is not. The original note: **No, and it never had. Fixed 2026-08-12.** `owned_species` was built and tested in Phase 3 and never passed into the bot's `PlayerState`, so every counter card in the 2026-08-11 session said *"I haven't read your Pals"* — including the ones the player pressed feedback buttons on. Now polled on the watcher's own five-minute cadence and shown on `/palintel status`; the reference save reads **194 owned characters**. **Every Q5 reading from that session was taken with the roster filter off.** |
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

- ~~**Player level is permanently `None`**~~ — **wrong for the host, corrected 2026-08-13.**
  The per-player level really is behind a stale `Level.sav` decoder, and that was written
  down in three places as though it settled the question. **`LevelMeta.sav` states
  `HostPlayerLevel` outright** — 2 KB, no custom decoders, no type hints. The reference save
  reads **61** against the floor Q6 infers of **57**, and those four levels were withholding
  **30 researchable technologies**, among them the Large-Scale Electric Egg Incubator. Only
  the *host's* level is stated; a joining player still gets the floor, and handing them the
  host's number would be the cross-attribution M1 exists to prevent. Fifth instance of this
  project's recurring pattern: the data was there and nothing asked.
- `min_player_level` / `danger` shipped **uncalibrated**, and **is now live for the host**
  — `player_level` reaching `PlayerState` turns on `find_resource_nodes`'s level filter,
  which had never fired. Measured before shipping: the highest `min_player_level` in the
  data is **60**, so at the stated 61 it filters **zero** of 8,665 clusters. Had the floor
  of 57 been wired in instead it would have hidden **657**, including 330 stone — which is
  the argument for the stated number over the inference, not merely a convenience. It
  becomes a real filter for a lower-level player, where the rule is still uncalibrated and
  the card still reports how many it withheld. — the rule asks for ~20 nodes of
  known difficulty read in-game and has had none. **It has one now, from 2026-08-12, and
  it was expensive**: a level 68–72 spawn area is lethal at this player's level, and the
  card that named it said nothing about that. One reading is not a calibration, but it is
  the first evidence this rule has ever had, and it arrived as a death rather than a note.
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
- ~~**Base camp positions are parsed out of an undecoded blob.**~~ **Now has a second
  source, 2026-08-13.** `BaseCampSaveData` has no decoder in 0.24.0, so `saves.base_camps`
  scans for a unit quaternion followed by an in-bounds translation — a structural check
  rather than a fixed offset, but a scan, and it had only ever been checked against itself
  ("3 of 3 on one save"). **The guild states its camps outright**, and `check_base_camps`
  now compares the two **camp by camp rather than by count** — a total would pass if the
  scan found a phantom and missed a real one. They agree on all four camps across both
  worlds: solo 3 of 3, co-op 1 of 1, same ids. Same standard `build_bosses.py` holds its
  two sources to. A disagreement is logged as a warning and shown on `/palintel status`;
  agreement stays silent, because a card reporting every check that passed buries the one
  that did not. The guild parse is **fail-closed** — an unexplained field non-zero, an
  implausible count, or any camp id the save does not hold discards the check rather than
  guessing, since a check that can be wrong is worse than no check.
- **Work-suitability levels are unverified against the UI.** `WorkSuitability_*` runs
  1–8 with one Pal at the top of each job. Lamball's 1/1/1 matches the game exactly, so
  the scale is probably the displayed one — but nobody has opened the Paldeck and counted
  the icons on a high-level Pal, and *"Anubis, Mining 6"* is wrong on a card if the game
  shows 4. **A one-glance check settles it**, and until it happens the cards print the
  number and never call it a star count.

---

## Next

**0. Play again, with the five fixes in.** Every one of them changes what a card says and
**none has been seen in play** — the `Field alpha:` and `Nearest:` rows, the spoken
coordinate, the restatement decline, and the feedback modal. The fixes are exactly as
unplayed as Phase 4 was this morning, and this project's own record is that built-and-
verified is not the same as observed. Specifically worth asking:

- *"Where can I find Anubis"* again, and any Pal with a field alpha. Does the extra row
  read as helpful or as clutter? It is one more line on a card whose density is already an
  open decision below.
- Any Pal you are near. Does `Nearest:` fire when you want it and stay quiet when you do
  not? The half-distance bar is a **chosen** number and play is the only thing that can
  argue with it.
- *"Rate the spot at 185, negative 475"*, off the in-game map. And one deliberately
  unreadable — *"rate the location at 321-500"* — which should now ask rather than answer.
- **Press the new button.** It is the one change here that cannot be verified without you,
  and the case it was built for is the one where the card looked fine at the time.

**0b. Discord voice receive is live and completely unplayed** (2026-08-13). It is now the
configured source. What to watch, in order:

- **Does the wake word fire at all?** The whole path is new below `SpeakerStream`, and its
  failure mode is silence. If nothing responds, set `voice.source = "mic"` and say so —
  that is one word and it is why the flag exists.
- **`/palintel status` carries receive counters** on this source: `rx ok / failed / opus
  err`. `opus err` climbing *while* `ok` also climbs is the signature of partial
  corruption, which is the failure that sounds fine. All three should be visible after the
  first person speaks; none at all means no packets are arriving.
- **Latency.** There is a network hop before the wake word now. The p95 is already failing
  at 6.2s against 2.5s and this can only add to it — measure before deciding anything.
- **Attribution.** Ask by voice, then follow up in text, and check the follow-up resolves.
  On this source it should work for *anyone* in the channel, without `voice.speaker`.
- **Two people at once**, which no version of this has ever done. `SpeakerStream` keys by
  speaker and nothing mixes, but that has been an assumption since Phase 0.

Turn **off** Discord's Noise Suppression, Echo Cancellation and Automatic Gain Control if
anything sounds wrong — Krisp is aggressive and the DAVE work found it suppressing pure
tones outright.

**0c-2. No save-age check existed anywhere — found AND FIXED 2026-08-13.**
`PlayerSnapshot.read_at` is
when *this process* read the file; `_mtime` is captured in `poll()` only to detect change and
is never stored or tested for age. So with the game closed the bot answers *"where's the
nearest coal"* against whatever position the save last held — and `/palintel status` reports
*"read 3s ago"*, which is reassuring and **about the wrong clock**. A coordinate card built
from a week-old position is byte-for-byte identical to one built from a live one, in the one
class that sends the player somewhere, and this project has already shipped a card that got
the player killed by naming a real place. `st_mtime` is preserved across SMB and cloud sync
alike, so the true age is available and simply never read.

**Fixed.** `PlayerSnapshot.written_at` comes from the file's mtime (stat'd *before* the read,
so a save rewritten mid-parse reads as staler rather than fresher); `player_coords()` returns
`None` past `MAX_POSITION_AGE`; `describe()` reports the save clock and says outright when the
position is not being used. Verified end to end against both real worlds on this disk — the
solo save (15.9h old) and the co-op save (33 days) are both refused, and both return
coordinates under a raised bound, which is what proves the gate refused rather than the read
failing. `age()` returns **None** for an unknown mtime rather than a large number, so a failed
`stat` does not silently read as "ancient". Nothing bypasses it: `cli.py` uses the same
accessor, and no other module touches `snapshot.map_coords`.

**Position only, deliberately.** The roster, technologies and base camps are slow-moving — you
catch a Pal every few minutes at best and move a base almost never — so gating them would throw
away good answers to prevent an error they cannot make. It is also what keeps a shared or
synced save worth reading at all (see the multi-user doc §4.1.3).

**`MAX_POSITION_AGE = 900s` is a bound, not a calibration**, and the one number here still
wanting evidence. Nobody has recorded Palworld's autosave cadence; the backup directories on
this disk are 10 minutes and an hour apart, which brackets it without pinning it. So it is set
comfortably longer than any plausible interval — the failure it must not cause is refusing
"nearest" to a player sitting still between saves. **One session's mtimes settles it**, and it
should tighten to ~3 autosave intervals once there is a number to multiply.

**0c. A concurrency defect the multi-user discovery found — FIXED 2026-08-13.**
`last_usage` was instance state on a shared router, read off `pipe.router` *after*
`run_in_executor` returned. The default executor has several workers, so two overlapping
queries interleaved and one was charged the other's usage — or `None`, which logs a **model
answer as a $0 fast-path answer**. This was the 2026-08-12 ledger bug returning by a
different mechanism: same two figures wrong, same direction that drains a prepaid balance
early, same wrong `path` label in the capture log. Reachable whenever text and voice
overlapped; routine the moment a second person can ask.

**Fixed by removing the shared slot rather than by guarding it.** A property fixed the 2026-
08-12 version because that staleness was between *calls*; this one is between *threads* and
no property can. Usage now travels on the returned `ToolCall`/`Decline`, and `Outcome.usage`
is stamped from the router's own return value in `handle` — not from `outcome.call`, because
`_dispatch` builds a fresh `Decline` when a model names a tool and omits a required argument,
which would have dropped the charge for exactly the queries the router found hardest.
`last_usage` survives for `score_router.py` and `router_variance.py`, documented as
single-threaded-only. **The regression test asserts the OLD read is wrong under the same
interleaving**, so it bites rather than agreeing with the new code.

Still open in the same family: **`Memory._by_user` has no lock**, unlike `ActivityLog`, and
`recent()` mutates the deque it iterates. A narrow race today, routine under multi-user.

**0d. Multi-user: M0–M2 and M4 SHIPPED 2026-08-13, M3 split and half deferred.** Designed
and built the same day — [`Docs/multi-user-design.md`](Docs/multi-user-design.md) is the
design, and it is annotated with what construction changed. **None of it has been played.**

| | |
|---|---|
| **M0** `0a527e2`, `8b2e2f0` | Usage travels on the returned call, not a shared router (a concurrency defect that re-created the 2026-08-12 ledger bug); the staleness gate; `Memory` takes an RLock |
| **M1** `8ac7d45` | Every `Players/*.sav` read rather than the newest. Identity binding by in-game name, `/palintel who` and `/palintel iam`. **Unbound resolves to nobody, never the host** |
| **M2** `3b892d1` | Roster is `carried ∪ guild containers`. Solo still reads **195**; co-op gives `Rui` 35 and `OutofLuck` 41, never 53 |
| **M3a** `c43f8d8` | The guild's own camp list as an independent check on the quaternion scan — agrees camp-by-camp on all four across both worlds |
| **M3b** | **Deliberately not built.** Both worlds hold one guild, so no available data distinguishes working code from broken. M2's shared set carries the same assumption |
| **M4** `ea2714c` | Per-user spend, latency persisted per speaker, capture attribution |

Exit criteria were met against the real saves, not fixtures: `Rui` answered at 35
technologies and 83/7 points, `OutofLuck` at 61 and 59/8, an unbound third person at
nothing, and the solo world unchanged throughout. **What that does not say is that anyone
has used it** — `/palintel iam` has never been typed, and two people asking at once has
still never happened.

The discovery that produced all of it, kept because the reasoning is the expensive part:

The headline is that `Pipeline.handle`
already takes `(utterance, state, who)` and the single-user assumption is eight lines in
`bot._answer`, and that **position, technologies and both point pools are already one file
per player** in `Players/*.sav` — `newest_player_save()` is the only reason they are not.
The finding that changes the design: the save does state Pal ownership (`OwnerPlayerUId`,
516 of 559 entries, joining exactly to the player save's `PlayerUId` once UE Guid byte
order is handled) — **but filtering on it drops the roster from 195 species to 184 on a
save with exactly one player in it.** The 43 entries with no owner all carry `SlotIndex`:
they are base-camp and Palbox Pals, which belong to the *guild*, and six of the eleven lost
species are ordinary Pals `counters.py` would shortlist. A perfect, well-formed, silent 5.6%
loss — the failure mode this project keeps meeting.

**And unlike the ADR-0008 gate, this one did not need a session — the two-player world was
already on disk.** `44403D77…`, players `Rui` and `OutofLuck`, guild `Foobar`. It settled
three of the four claims the design listed as uncheckable and **refuted one**:

- **Cross-attribution is live, and Q6 is the sharpest case.** The two players have **35 vs
  61** technologies and **83/7 vs 59/8** points. `newest_player_save()` returns whichever
  file the game wrote last, so *"what should I research next"* is already answered against
  the wrong player's tree, on a card that is entirely well-formed.
- **The roster over-count is 66%.** Union 53; `Rui` owns 32, `OutofLuck` 39, 19 shared. A
  counter card tells `Rui` it "checked 53 of your Pals" and will shortlist Pals the other
  player owns.
- **Refuted: the obvious field is the wrong field.** `CharacterSaveParameterMap`'s *map key*
  also carries a `PlayerUId`, and it splits **239/1** — it is not ownership. Reading it
  gives a clean, total, error-free misattribution. Only the blob's `OwnerPlayerUId` is the
  owner. Same for the guild's handle list, which is keyed the same way, so the guild-container
  join has to run through `CharacterContainerSaveData` instead.
- **Still untested:** the two players were standing 2 map units apart at the last save, so
  *position* divergence — the class where a wrong answer sends someone somewhere — is not
  evidenced. And the trap's size tracks base infrastructure (11 species lost on the
  3-camp single-player world, 1 on the 1-camp co-op world), so **neither save is evidence
  about a mature co-op world**.

**And then the framing itself turned out to be wrong, which is the finding that matters
most.** The design asked "one world or separate worlds"; the real question is **whether the
save is on the machine running the bot**, and head count has nothing to do with it. The
group's *most recent* multiplayer world (`8C0191…`, 2026-08-02) is **one world, two people,
and completely unreachable** — a friend hosts it and this machine holds a single
`LocalData.sav`. A byte scan of its 5.2 MB finds **none** of `LastTransform`,
`UnlockedRecipeTechnologyNames`, `TechnologyPoint`, `bossTechnologyPoint`,
`TowerBossDefeatFlag`, `CharacterID`, `OwnerPlayerUId` or `NickName`; it is a static id
catalog, near-identical across all three worlds on disk. So **M1–M3 would have shipped and
done nothing for the way this group actually plays**, which the original framing would not
have caught until after the work. On that world 6 of 13 classes are unaffected, 5 degrade to
declines they already implement, and Q6 stops working (honestly — `unlocked=None` reaches
`progression_card` and declines, rather than falling through to a level floor of zero and
recommending tier-1 tech to a level-57 player).

The requirement is **not** "the bot runs on the host's PC" but "the bot's process can open
the save directory", and that admits a cheap first try: **the host shares the save folder
read-only and `save_dir` points at a UNC path.** `Config.save_dir` is already a `Path`,
nothing resolves it, and the watcher only globs, stats and reads — so this should need no
code change at all. Untested against a real share. Fall back to running the bot on the host;
accept the degradation if neither is convenient; **do not build a save-shipping agent** until
a session has shown the degradation is not enough.

**1. Then the three found-and-not-fixed items**, in the roadmap and repeated under the
session block above: the Q6 narrowing that defers, the `item_source` card title, and Q7
retrieval diversity. Each is small; each wants its own sweep rather than a ride-along.

~~**2. Persist latency.**~~ **Done 2026-08-13 (M4).** `activity.py` kept a one-hour
in-memory window and wrote nothing, so the 2026-08-12 voice p95 of 6.2s against a 2.5s
budget — a Phase 1 exit criterion still recorded as failing — existed only in a status line
pasted into a chat log. It now appends `data/sessions/<session>/latency.jsonl` beside the
spend ledger and the clips, **attributed per speaker**, so a party session can show whose
queries are slow rather than one blended population. Only *timed* events are written:
counters are cheap to recompute and worthless after the fact. A write failure logs and
carries on — a full disk must cost a measurement, never an answer. **The p95 itself is
still unmeasured since the fixes**; what changed is that the next session's number will
survive it.

**[`Docs/test-plan.md`](Docs/test-plan.md) is the full inventory** — every untested class
and every reading that needs retaking, with the exact wording to say, what each item is
testing, and what to expect, all of it produced against your live save. The summary below
is the same list at a glance, with what the 2026-08-12 session actually did to each:

- ~~**Q6.**~~ **Asked, six times, all fast-pathed.** The level floor held. What it found
  instead is that a narrowing it cannot map is **dropped rather than deferred** — see above.
- ~~**Q4.**~~ **Asked. Not walked to.** Siting and rating both answered; the coordinate
  parser defect was found here. Buildability is still unchecked, and *"walk to the
  coordinate"* is still the item — this session walked to a **spawn** coordinate instead,
  and that is what found the lethal card.
- **Q7.** *"How does sanity work"*, *"what is item rot"*, *"explain pal effigies"* — all
  asked and all answered. **Zero declines in ten corpus questions**, which is weak evidence
  rather than strong: the interesting result was supposed to be what it declines, and it
  declined nothing while producing one false hit at match **1.00** (*"how do I be Victor"*,
  a mangled counter question, answered with a lore diary from the fast path). Ask it things
  it should refuse.
- ~~**Q5, for the first time with a roster.**~~ **Confirmed** — *"checked 143 of your
  Pals"*, tower/alpha split correct on all three. See the roster row above for the 14
  species that count silently excludes.
- **One in-game glance:** unlock the Breeding Farm. It costs 2 of your 40 ancient
  technology points and every other requirement is already met. That opens the ADR-0008
  gate, which is the largest single gap in this project. **The 2026-08-12 session was
  largely about this** — five of the 52 utterances asked how to unlock it, what breeding
  needs, how to get the Egg Incubator and where to find cake — so the block is now
  *"working out the in-game steps"* rather than *"deciding to"*. Two of those questions
  are the ones the product answered least well: cake resolved to a 1% Lovander drop
  instead of a recipe, and *"how do I assign a pal"* got the goal restated back.

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
| **Q7: synthesis, or keep quoting?** — *and play showed the cheaper answer first* | Today a Tier 3 card quotes the game verbatim, so no model touches the text and ADR-0011's drift failure cannot occur. **2026-08-12 produced the first question that needs two chunks** — *"how do I assign a pal to the breeding farm"*, where the Breeding Farm guide restates the goal and the `Base` chunk holds the mechanism. But the card **already renders a second quote**, and it spent that slot on a near-duplicate. So the measured need is **retrieval diversity**, which costs no model at all, and synthesis remains unshown. Try diversity first; if it still cannot answer, that is the evidence for synthesis. |
| **Q4: is the computed version enough?** | The roadmap's Q4 was twenty curated sites with prose rationale; what shipped is "what falls inside a base's radius", because the curated version needed invented flatness scores and community prose. If you want the curated half, it needs a source you trust and a way to verify it. |
| **Should `pal_info` answer questions it cannot answer?** | Measured 2026-08-12: it absorbs *"how much stamina does Rinjishi have"* and *"is loopmoon worth levelling up"* — questions with no class, where a summary is arguably better than a decline and arguably the wrong-class failure the first play session named. The decline policy was rebalanced on 2026-08-11 toward answering, on the finding that declining an answerable query is also a failure; this is the same trade seen from the other side. **Your call, and the first play session is where it will feel wrong or fine.** *2026-08-12 produced one instance and it is a mild one*: **"how do I unlock Anubis"** routed to `get_pal_info` — an unlock question about a Pal, absorbed because it names one. No feedback button was pressed on it. One data point, pointing the same way the measurement did. |
| **Set `cost.balance_usd`** — *now safe to* | Spend is logged per query and totalled, but the balance is 0 so nothing is deducted. **Do not set this from a reading taken before 2026-08-12**: the ledger over-reported by 3.8× and would have warned you empty with two thirds of the money left. Fixed and regression-tested; a real session costs about **$0.09**, not $0.33. Put what you actually loaded onto the key in `config.local.toml`. |
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
- ~~**Discord voice receive** — upstream-blocked on DAVE; party members cannot ask by voice~~
  — **wired up 2026-08-13, and the blockage was never DAVE.** DAVE decrypts **99.8%** of
  packets. py-cord 2.8 shipped a new `voice/receive/` package against the old
  `sinks/core.py`, so `start_recording()` raised before a single packet was read, for
  every sink including py-cord's own. **Thirteen** defects below that, fixed in
  [`PyDiscordDave`](../PyDiscordDave/README.md); no Python MLS work was needed and this
  project does none of the cryptography.

  **Played 2026-08-13.** Thirteen spoken questions answered end to end from a voice
  channel — resources, spawns, counters, tech, base rating — at 360-547ms STT and
  47-3188ms routing, attributed to the speaking member rather than to a configured name.
  Receive is measurably clean: every packet delivered, no discards, ~180ms added latency.

  Two of the thirteen were **this repo's**, and both were invisible until Discord audio
  arrived:

  - **`UtteranceBuffer` had no clock.** It counted *quiet frames*, which assumes silence
    still produces frames. A microphone always does; **Discord stops transmitting
    entirely** when a speaker stops. `push` was never called, the counter froze, and an
    utterance stayed open until that person spoke again — two questions 30s apart arrived
    as one, and the delay read as a transport fault for most of a session. `tick()` now
    closes on wall-clock silence, driven from the listener's event loop.
  - **The detector carried audio across the gap.** `_tail` plus openWakeWord's rolling
    context spliced pre-silence audio onto the front of the next "hey pal", exactly where
    the model is most sensitive. `WakeWord.reset` existed for this boundary — *"call
    between utterances, not between frames"* — and nothing called it.

  **What remains. None of it is transport:**

  - **Wake-word recall has no number.** Fires scattered 0.10-0.95, and misses never reach
    the log at all, which is why it *feels* worse than the log looks. The one improvement
    measured (floor 0.11 → 0.34) is **confounded**: the microphone changed from desk to
    headset in the same session and input level roughly doubled. A clean A/B needs the
    same mic on both arms. **Do not retrain anything before that number exists.**
  - **Wake score does not predict transcription quality.** 0.95 produced *"PayPal…
    Forks"*; 0.20 and 0.24 produced flawless transcriptions. It is not a proxy for audio
    quality, and treating it as one sent a session down a wrong path.
  - **Mistranscriptions are almost entirely proper nouns** — `Forks`/`Forts` → Foxparks,
    `Jitra` → Jetragon, `PayPal` → Hey pal — while ordinary English is near-perfect. That
    is the lexicon-alias entry above, not an audio problem, and the candidates should come
    from a real play session rather than from test utterances.
  - **`artwork.py:52` map render crash**, surfaced 2026-08-13; the card ships without its
    map. Unrelated to voice.
  - **`mic.py` stays the default and the fallback.** Nothing here should be the only way
    in ([ADR-0004](Docs/adr/0004-wake-word-activation.md)).

  One habit this earned: after these fixes **a silent failure no longer raises**, so a
  counter moving is the only evidence there is — hence `discord_voice.stats()` and the
  per-minute health line. A fix shipped mid-session was fabricating 1.2s of synthesised
  audio into live speech while every health counter read green; what caught it was tagging
  packets with the decision that produced them, not reasoning about the symptom.

  **This entry was written from py-cord's warning message, not from a measurement.** That
  warning says reception is "currently broken due to Discord's DAVE protocol", which is
  true about the symptom and silent about the cause — and the cause got recorded here, in
  two ADRs, in `mic.py`'s docstring, and in a config validator that *rejected*
  `voice.channel_id` outright. Same shape as the crude oil card the day before: a
  plausible cause inferred from an absence and then published as a fact.

  `voice.source = "mic" | "discord"`, mic still the default. **Untested in play** — see
  Next. The one thing it restores beyond the original design is that attribution stops
  being configuration: every packet carries its member, so `voice.speaker` is not
  consulted and per-user memory (ADR-0013) holds for everyone in the channel rather than
  for one person by declaration.
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

**A fifth, from 2026-08-12, and it is the purest form of the pattern this file keeps
naming.** `PakExtract`'s resource derivation filters on `BP_PalMapObjectSpawner*`. Crude
oil has none, so `_resources.py` recorded that it "has no overworld spawner class — it
comes from oil rigs", `cards.NOT_PLACED` turned that into a sentence for the player,
`02-data-model.md` recorded it as *a correction the data forced*, and four tests asserted
it. There are 185 oil fields; they are `BP_LevelObject`s. **An absence in a filtered search
became a claim about the world and then propagated into two documents, a dataset, a card
and a test suite** — every step locally sound, and found only because a player had stood on
one. *"I searched for it" is only as strong as the term searched for*, for the fourth time,
and this is the first time the conclusion was published as prose rather than left in data.

**And a sixth, which is the one this file itself is most exposed to: the project measuring
itself wrong.** The spend ledger over-reported by 3.8× and claimed 55 of
56 queries reached the model when 16 did, because a router wrapper forwarded a sticky
attribute. Nothing looked broken — the ledger was well-formed, the totals added up, and the
line on `/palintel status` was confident. It was caught by reading the raw `costs.jsonl`
against the capture log and noticing that "32 fast" and "55 reached the model" cannot both
be true of 56 queries. **The two files disagreeing is what found it**, which is an argument
for keeping both rather than deriving one from the other.

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
