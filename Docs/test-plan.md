# Gameplay test plan — the untested surface

*Written 2026-08-12, after Phase 4 landed. This is **not** the
[play session protocol](play-session-protocol.md), which is a latency-grading script whose
criterion has already been measured and failed. This document is the inventory of
everything that is **untested, or was tested under conditions that invalidate the
reading** — and it is the larger of the two lists.*

**Why there is so much.** Three classes shipped on 2026-08-12 and none has answered a real
question. Four more shipped on 2026-08-11 *after* the session that was supposed to exercise
them. And every counter answer in that session was produced with the owned-Pal roster
disconnected, so those readings measured something other than what they appeared to.

---

## How to read an expectation here

Every "Expect" below was produced by running the query against the **fast path only**
(`--router stub`) on your live save, on 2026-08-12. Two consequences:

- **Numbers are real, not invented.** "checked 143 of your Pals" is what your save
  produces. A different number means the roster read moved.
- **A decline in this document may be answered in play.** The model sits behind the fast
  path, so anything marked *"declines on the fast path"* may come back with a real card
  from the model. That is usually correct behaviour, and where it is not, the plan says so.

`/palintel recent` is what tells the two apart: `~0.1s` is the fast path, seconds mean the
model answered.

**Capture and feedback are already on** in `config.local.toml`. The three buttons under
each card must be pressed **in the same session** — the view does not survive a restart.

---

## If you only have twenty minutes

Run **Block A** and **Block H2**. A is the only block whose results are currently *wrong*
rather than merely missing, and H2 is two clicks that unblock a whole phase.

---

## Block A — Q5 counters, retested because the roster was disconnected

**What is being tested:** whether counter shortlists are now filtered to Pals you actually
own. `saves.owned_species` was built in Phase 3, unit tested, and never passed into the
bot's `PlayerState`, so **every counter card you saw on 2026-08-11 was unfiltered** while
the card politely said it had not looked. Your save reads 194 owned characters, 143 of them
with a typing row.

**Known issues / watch for:**
- The footer must say **"checked 143 of your Pals"**, not *"I haven't read your Pals"*. If
  it still says the latter, the watcher is not reaching the roster — check
  `/palintel status` for the new **Roster:** line.
- Every recommendation must be a Pal **you can actually field**. This is the Tier 2
  guarantee; a name you do not recognise as yours is the failure the whole
  candidate-set discipline exists to prevent.
- The shortlist ties constantly — typing is the only thing scored, so five Grass Pals all
  read "deals 2x | takes half" and the footer says the order is arbitrary. Is that honest
  or is it useless?
- **The tower-vs-alpha preference was fixed *after* the session that found it.** A tower
  species must resolve to the tower, not the field alpha of the same name.

| # | Say | Tests | Expect |
|---|---|---|---|
| A1 | how do I beat Anubis | roster filter on a field alpha | *Anubis is Ground (field alpha)*, five Grass Pals, **checked 143 of your Pals** |
| A2 | how do I beat Victor | leader → tower, not the field alpha | title **How to fight Victor & Shadowbeak**, *"Victor's tower"*, Dragon counters |
| A3 | how do I beat Axel and Orserk | the game's own name for a fight | title **How to fight Axel & Orserk**, Ice/Ground counters |
| A4 | how do I beat Grizzbolt | the reversal that the session forced | must say **Zoe's tower**, not "field alpha" |
| A5 | what's Victor's weakness | possessive phrasing, added after the session | same card as A2, on the fast path |
| A6 | how do I beat Zenara | a boss with no element at all | an honest decline: *"Zenara's tower boss Astralym has no element"* — **not** an empty shortlist |
| A7 | how do I beat Bellanoir | a raid boss | a counter card, kind = raid boss |
| A8 | where can I find something to beat Anubis | chained dispatch: two cards, two tiers | **two cards** — a counter plan *and* an Anubis location |

---

## Block B — Q6 progression, never played

**What is being tested:** the first class that reads your technology state. Candidate set,
two currencies, and a **derived** player level.

**Known issues / watch for:**
- **The level is inferred and it is a floor.** Your save has no readable player level, so
  the card assumes *"at least level 57"* from the highest technology you have unlocked. If
  you are above 57, it is hiding things you can already research — and only you can say
  how much that costs. Saying your level in the question overrides it.
- **The two point pools must never be added.** Every line names its own currency: `3 pt` or
  `3 ancient pt`. You have 244 and 40.
- Ordering is **most advanced first, which is not a ranking.** Does that read as a
  recommendation you would act on, or as an arbitrary slice?
- The card ends with what it did *not* show — *"329 of 471 still locked are researchable
  now"*. Useful, or a wall of arithmetic?
- **Naming a technology is now its own class** (`find_technology`, built after this plan's
  first draft). It is the one place 588 names are matched, and they are matched *only* to
  the object of an unlock verb — because twelve technologies are named ordinary English
  (`Mine`, `Ranch`, `Mill`, `Sword`, `Sign`) and would wreck the lexicon if they ranked
  globally. **The thing to probe is theft in both directions**: does it grab a question
  that was about an item or a Pal, and does it decline one you meant for it?
- **Ambiguity is a decline, and it will look like a bug.** Five tiers of grappling gun and
  several cakes exist, and speech normalisation deletes the digits that separate them, so
  those defer to the corpus rather than guess a tier. Judge whether the fallback answer is
  useful or whether the silence is just annoying.

| # | Say | Tests | Expect |
|---|---|---|---|
| B1 | what should I research next | the bare question, no arguments at all | five level-57 technologies, footer *"assuming you're at least level 57"* + *"you have 244 pt and 40 ancient pt"* |
| B2 | what can I unlock at level 30 | a stated level beats the inferred floor | level-30 rows, footer says **player level 30** (no "assuming") |
| B3 | what should I spend my ancient technology points on | the currency filter, which the first build silently dropped | title **What to spend ancient points on**, every line `n ancient pt` |
| B4 | what weapon should I research next | goal filter → the game's own category | title *"— Weapon"*, Advanced Bow / Beam Sword / Guided Missile Launcher |
| B5 | what should I research for my base | the player's word for `BuildObject` | title *"— BuildObject"*, structures |
| B6 | what tech should I research for my mining pals | the one real collision — this says "pal" *and* a job word | a **technology** card, not a roster of mining Pals |
| B7 | how do I unlock the breeding farm | the named lookup, against your own save | **How to unlock Breeding Farm** — *"You can research this now"*, ✅ level 19, ✅ 2 ancient technology points, ✅ ForestBoss tower defeated |
| B7a | how do I get the egg incubator | something you already have | *"You already have Egg Incubator"* — a green card, no shopping list |
| B7b | what do I need for the breeding farm | the second frame, same class | the same card as B7 |
| B7c | how do I unlock a grappling gun | ambiguity between five tiers | **not** a technology card — it should fall through to the corpus or decline. A confident tier here is the bug |
| B7d | where do I get high quality pal oil | the theft this class already committed once | an **item** answer, never a technology card |
| B7e | how do I unlock Anubis | a Pal wearing an unlock verb | a Pal answer or a decline — the species guard runs before the name matcher |
| B8 | can you explain technology points | Q6 refusing a question it cannot answer | **not** a shopping list — this should reach Q7 (see D1) |

---

## Block C — Q4 base siting, never played, and one item needs your legs

**What is being tested:** where to put a base so named resources fall inside it. The radius
is `BaseCampAreaRange`, 3500 world units = 7.63 map units, read from the pak.

**Known issues / watch for:**
- **The card cannot tell you if the ground is buildable.** Nothing in the game files says
  whether a spot is flat, underwater, or in a no-build zone. Every card says so. **C5 is
  the test that decides whether this class is worth having.**
- Near-duplicate sites are collapsed — deposits cluster tightly, so the raw top three were
  coordinates five map units apart. If you see two suggestions that are obviously the same
  place, the deduplication is too loose.
- The multi-resource case is the new capability. A single-resource question is close to
  what `find_resource_nodes` already did.
- The radius itself has **never been measured in game**. It was corroborated only by
  applying it to your three real base camps, which contain 3, 2 and 1 clusters.

| # | Say / do | Tests | Expect |
|---|---|---|---|
| C1 | where should I build my base for coal | the placement cue, single resource | three coordinates, e.g. **(321, 500)** 9 coal; footer *"within 7.6 map units"* + the buildability caveat |
| C2 | where should I put a base for ore and stone | **the reason this class exists** — one circle, two resources | e.g. **(185, −475)**, 36 stone + 1 ore, and *"872 of 4427 spots reach all of it"* |
| C3 | best base spot for wood | a different placement phrasing | wood sites, e.g. (137, −444) 34 wood |
| C4 | where's the coal near my base | **the guard** — same words, no placement verb | an ordinary **Coal locations** card, *not* a base site |
| C5 | *walk to the top coordinate from C1 or C2* | buildability, which no offline work settles | Can you actually place a Palbox there? Is it a cliff, water, or a no-build zone? |
| C6 | *stand there and look around* | whether the radius is believable | Do the promised deposits sit inside the base circle the game draws? |
| C7 | how good is my base location | **the rating class, never played.** Reads your base camps out of `Level.sav` | **Your base 1 — 4 of 4** at (229, −487): flat ±2.0m, water 11 units, 41 deposits (better than 91%), and *"the game marks this area — nearest is 1 unit away"* |
| C8 | rate this base location | the other reading — where you are *standing*, not where you built | A card for your current position. **If it rates your base instead, the two readings are the wrong way round** |
| C9 | *stand somewhere you think is bad and ask C8* | whether the criteria track your own judgement | Does 1-of-4 feel like a bad spot? This is the only check on whether the rating means anything |
| C10 | *compare the card for base 2 with how it plays* | the marked-area signal | Base 2 scores 2 of 4 and is 76 units from any marked area. Does it in fact feel worse to run than base 1? |
| C10a | is this a good spot for a quartz base | the resource filter — **the one case where a named entity is the filter, not a reason to abstain** | The criterion becomes *"quartz in range"*, scored against **quartz sites only**. The "In range" line still lists everything, with quartz in bold |
| C10b | how good is my base location for ore | the same, on your own base | **3 of 4** — your main base holds 1 ore, which is the 0th percentile of ore sites, while the *general* question on the same base is 4 of 4. If both come back identical the filter is not reaching the criterion |
| C11a | *take a coordinate from C1's card and ask* **rate the base location at (321, 500)** | **the loop closing** — siting answers in coordinates, so a rating has to accept one | A rating card for that exact spot. Needs no save, so it works from anywhere |
| C11b | rate this spot at 9999, -9999 | the off-map guard | **"That's off my map"**, not "0 of 4". A zero score reads as a judgement about a bad site; the truth is the coordinate isn't somewhere it can speak about |
| C11c | rate this base at level 60 | **the false-positive guard** — two numbers that are not a position | Must **not** produce a coordinate card. `"level 20, 30 stone"` is the phrasing that broke the first version |
| C11 | what makes a good base | the general question — **no place, no save needed** | The four criteria with the source of every bar, then three things it says it **cannot** check. **The failure to watch for is the game's own *Base* help page instead**: that explains the Palbox and says nothing about siting, and it scores well on the words |
| C12 | *read C11's "what I can't check" list* | whether the honesty is useful or is an apology | Buildability, raid safety, how it plays. Does naming them help you trust the other four, or does it just read as excuses? |

---

## Block D — Q7 corpus lookup, never played, and the declines are the data

**What is being tested:** Tier 3. The game's own help text, **quoted verbatim** with a
citation. No model touches the words.

**Known issues / watch for:**
- **What it declines matters more than what it answers.** The seventeen questions it
  passes in the harness were written using the help guide's own vocabulary. Its five known
  failures are all paraphrases — the game has a *Death* entry and a player says "die" —
  and those score in the same band as questions with no answer at all. **Every decline in
  your own phrasing is evidence for or against building an embedding index.** Write the
  wording down.
- The corpus is the game explaining its own mechanics. It has nothing about playing
  *well*: no tier lists, no optimal routes, no best layouts. That is by design and it is
  the honest limit.
- A blue card is reference, not fact and not advice. Does the colour read?
- The second passage ("**Also:**") only appears when it is nearly as good an answer. If it
  ever looks like padding, the margin is too loose.

| # | Say | Tests | Expect |
|---|---|---|---|
| D1 | can you explain technology points | Q6 and Q7 composing | the **Ancient Technology** help page, quoted, plus an "Also:" from **Technology** |
| D2 | how does sanity work | a mechanics lookup | the **Sanity** help page verbatim, cited `— Help guide: Sanity` |
| D3 | what is item rot | short, exact | **Item Rot**, quoted |
| D4 | how does the breeding farm work | must be the *help page*, not the Ancient Farm structure | `— Help guide: Pal Breeding Farm` |
| D5 | what are pal effigies | another mechanic | **Pal Effigies** |
| D6 | what happens when I die | **a known miss** — "die" against a *Death* entry | expected to decline. Does the model rescue it? |
| D7 | how do I raise a pal's rank | **a known miss** — "raise" against *Pal Rank & Essence Condensers* | expected to decline |
| D8 | what's the best base layout | out of corpus, and a real question | **must decline.** The game never says this, and quoting something adjacent would be the failure a citation makes worse |
| D9 | *five questions of your own, about mechanics* | the only unbiased sample there is | note the exact wording of anything it declines |

---

## Block E — `pal_info` and "can I ride X", built after the last session

**What is being tested:** the shape that was **the most-asked in the first session and did
not exist** — nine of forty-one utterances, seven of them answered by the wrong class.

**Known issues / watch for:**
- The failure it was built to remove is a *wrong class*, not a decline: a location card for
  "tell me about Shroomer", a counter plan for "who is Victor". If any of these comes back
  as a map or a fight plan, the fix did not hold.
- The rideable line is **unconditional**, including when the answer is no — silence must
  not carry that meaning.
- A leader is deliberately **not** this class: there is no info card for a human, and one
  showing a Pal's stat line for "who is Victor" would be the same wrong-class failure
  wearing a different hat.

| # | Say | Tests | Expect |
|---|---|---|---|
| E1 | tell me about Shroomer | the canonical case | a summary card: elements, levels, work, rideable, ranch, drop count |
| E2 | what level is Penking | a narrower info phrasing | an info card, not a location |
| E3 | who is Victor | a leader, which this class must **not** claim | anything but a Pal stat line — a counter card is acceptable |
| E4 | can I ride Azurobe | yes-case | *"Rideable, saddle at player level N"* |
| E5 | can I ride Anubis | **no-case, stated out loud** | *"Not rideable - it has no saddle."* |
| E6 | how good is Jetragon | an opener that names what is wanted | an info card |

---

## Block F — attribute and mount search, now that the roster works

**What is being tested:** the set-difference half of mount search, which has never run
because it needs the roster; and attribute search, which shipped and was exercised but
whose fixes landed afterwards.

**Known issues / watch for:**
- **Speed ordering was confirmed correct by you on 2026-08-11.** This block is about the
  parts that were not.
- "level" means the **Pal's** level everywhere except a mount question, where the saddle
  states a player gate. Two words on two cards; does the difference read?
- An element word the parser cannot attach must **defer**, not silently drop the filter.
  F5 is that test: "dragons" is not "dragon pal".

| # | Say | Tests | Expect |
|---|---|---|---|
| F1 | which mounts don't I have yet | **the set difference, first run ever** | *"Mounts you don't have yet"*, **5 of 68 shown** — Shaolong, Eidrolon, … |
| F2 | I need a mining pal | attribute search, job axis | Aegidron 8, Astegon 7, Blazamut 7 — *"Mining level is the game's own"* |
| F3 | which pals can ranch | the trailing-verb cue play found | *Pals for Farming* — Sibelyx Primo, Dumud Gild, Shroomer |
| F4 | what electric pals are around level 60 | the widening message | *"Nothing spawns at exactly level 60 — these are the closest"* |
| F5 | which dragons can I ride at level 60 | **the dropped-filter bug** that reverting the bare-plural rule found | either a Dragon-filtered mount list, or a decline — **never** a full mount list titled "Mounts" |
| F6 | the fastest swimming mount at level 60 | medium + player level | water speeds, *"at player level 60"* |

---

## Block G — guards that must NOT fire

**What is being tested:** the abstentions. Each of these is a case where answering
confidently would be worse than not answering, and several encode a bug that was actually
observed.

**Known issues / watch for:** a *decline* here is a pass. A confident card is the finding.

| # | Say | Tests | Expect |
|---|---|---|---|
| G1 | how about breeding Anubis | an opener borrowing the last turn's verb (say it right after G0: *where can I find Chillet*) | **a decline** — never a location card |
| G2 | which pals can ranch → *then* → which ones are for level 60 | **the live refinement trap**: `"ones"` scores **0.80 against stone** | a decline. **A stone locations card is the bug**, and it is the one thing in this document that is a known landmine rather than a guess |
| G3 | where can I find adamantium | an unknown token | a decline naming what it *can* find |
| G4 | is Prixter any good against the first tower | the named entity is the *attacker*, not the target | must **not** produce a plan for fighting Prixter |
| G5 | where should I build my base | a placement verb naming no resource | not a base-site card — a base is built *for* something |
| G6 | where should I build a base for crude oil | a resource with no placed nodes | must not answer about the rest of the sentence and drop crude oil |
| G7 | what does Gidra and Dromatide drop | two Pals, one slot — **accepted behaviour**, re-confirming | a Gildra drop card. You decided this is fine; check that you still think so |

---

## Block H — in-game checks that need no bot at all

| # | Do | Tests | Expect / outcome |
|---|---|---|---|
| **H1** | Open the Paldeck for **Anubis** or **Blazamut** and **count the Mining icons** | the work-suitability scale, shipped uncalibrated | The card says *Mining 6* for Anubis. If the Paldeck shows **6**, the scale is the displayed one and the caveat can be dropped. If it shows 4, every work number on every card is wrong |
| **H2** | **Unlock the Breeding Farm** — Technology menu, 2 ancient technology points | the ADR-0008 gate, and the largest single gap in the project | Every stated requirement is already met: level 19 (you are ≥57), ForestBoss beaten, no prerequisite, 2 of your 40 ancient points. The Egg Incubator is already unlocked. **If the menu refuses, that is itself a finding** — it means the table does not carry everything the game enforces |
| H3 | After H2, note whether a **tower-gated** technology behaves as the card predicted | the `EPalBossType::ForestBoss` → `BOSS_BATTLE_NAME_ForestBoss` join, which is an **inference on a key name** | Anything the card said needs a tower you have beaten should be buyable; anything it said needs one you have not should be locked |
| H4 | Stand at a `danger: low` node and a `danger: high` one | `min_player_level` / `danger`, uncalibrated since Phase 1 | Do the surrounding Pal levels match? The rule wants ~20 nodes of known difficulty and has had none |
| H5 | Ask for a resource in the **World Tree** region and walk to it | the tree-region transform, fitted only on MainMap landmarks | The 2026-08-11 walk verified five resources and **no tree-region node**. This is the last unverified transform |

---

## Block I — read the cards already in the channel (no new queries)

| # | Look at | Tests | Expect |
|---|---|---|---|
| I1 | The six `item_source` cards from the 2026-08-11 session | **the last item on the old protocol's list.** Ten queries were asked, all routed correctly, and **only Chillet's card was read back** — routing is confirmed and correctness is not | Do "who drops flame organs", "what drops wool", "who drops bone" name the right Pals? Scrollback, not a session |
| I2 | Any resource or Pal card with artwork | the maps/icons decision, still open | Text first, picture a moment later: responsive, or jank? Is the marker **on the thing**? |
| I3 | A Kingpaca card | the cross-region message | *"Map shows MainMap only — #3 is on another map"* — clear, or confusing? |
| I4 | Any Pal card | card density, an open editorial call | Do "Also drops from" and "Ranch:" earn their lines? |

---

## Block J — optional: re-grade latency

**Skip this unless you want the number.** The criterion is p95 ≤ 2.5s voice and ≤ 1.5s
text over ≥ 30 answered queries of **each** kind, and it was measured on 2026-08-11 at
**6.5s voice p95** with route p50 2.83s — 71% of the total.

Phase 4 added three fast-pathable classes, which should move it in the right direction, but
the roadmap's arithmetic still holds: p95 needs under 5% of queries reaching the model, and
`item_source` cannot be fast-pathed while items stay out of the lexicon. **The structural
blocker is unchanged**, so expect this to fail again unless the mix has shifted a lot.

If you want it: the typed block 9 in [the old protocol](play-session-protocol.md#block-9--the-text-pass-32-typed-not-spoken)
is what clears the text half; `/palintel status` shows `⏳ n/30` per kind.

---

## Afterwards

- `/palintel status` — and check the **Roster:** line specifically. It is new, and it is
  the thing whose absence made Block A necessary.
- `/palintel recent` — the last 12 queries with routing time. `~0.1s` is the fast path.
- **Press the feedback buttons in the same session.** The view does not survive a restart.
- Note verbatim anything mis-heard. The transcript is worth more than the fact of the miss.

**What this session should produce, in order of value:**

1. Whether counter shortlists are now Pals you own (Block A) — currently the only *wrong*
   output in the product.
2. Whether a base site is somewhere you can build (C5) — the one thing no offline work
   settles about a whole class.
3. The exact wording of every Q7 decline (D6–D9) — the evidence for or against embeddings.
4. Whether the Breeding Farm unlocks (H2) — opens the ADR-0008 gate.
5. Whether the Paldeck shows 6 Mining icons on Anubis (H1) — settles a caveat printed on
   every attribute card.
