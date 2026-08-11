# Play session protocol

*Revised 2026-08-10 for card artwork, the drop classes and the overworld-only node fix.
The Phase 2 blocks below are unchanged and still need running; blocks 6-8 and the ground
truth are new.*

A scripted session, because several things are open that only real play can close: the
end-to-end latency criterion (unmeasured across three phases), the datasets' correctness
against an actual map, and design calls a harness cannot judge.

**Three of them cannot be closed any other way, and two have been open all along:**

| Open question | Why no amount of offline work settles it |
|---|---|
| `art_post` p95 | The Discord upload round trip. Every render number is local. |
| Do markers land on the actual rock? | The transform was validated to ±3 map units against 7 landmarks, none of them a node or spawn area. |
| Does `item_source` work? | All 240 eval recordings predate the class, so no run measures it. Verified by hand only. |

**The script is not the point; the misses are.** Phase 1's most useful finding came from
reading verbatim transcripts of queries nobody would have thought to write down. Run the
script, then keep playing and ask whatever you actually want — `/palintel recent` is what
makes the unscripted half diagnosable.

---

## Before you start

1. **Set the speaker** in `config.local.toml` (already set to `Ruichan`):
   ```toml
   [voice]
   speaker = "<your Discord display name>"
   ```
   Without it, spoken questions and typed follow-ups land in separate memory threads and
   block 4 below fails for the wrong reason.

   **Also check `[cards] maps` and `icons` are true**, and that the assets exist -
   `/palintel status` says `maps+icons, 2 map regions`. `maps+icons requested, no assets`
   means the two-step build has not been run.

2. **Check `/palintel status`.** It should report the mic device, `hey_pal @ 0.1`, and
   `heard as <your name>`. If it says `unattributed`, step 1 did not take.

3. **Expect the latency picture to look worse than Phase 1's best session.** The fast path
   now carries ~61% of a two-class mix rather than 78%, because Q2 queries it cannot claim
   go to the model. That is predicted, not a regression.

4. **The router is now one consolidated tool** (`router.unified`), measured
   indistinguishable from the per-class registry (McNemar p = 0.73) and cheaper. If
   routing behaves oddly in a way the script does not predict, `router.unified = false`
   is the comparison - at the cost of `item_source`, which only exists in the new shape.

---

## What the numbers need

The criterion is **p95 ≤ 2.5s voice and ≤ 1.5s text, over ≥ 30 answered queries of EACH
kind**; declines are tracked but not graded.

That "each" is why this criterion has been carried forward twice. Blocks 1-8 are ~52
spoken queries, which clears 30 voice at a normal decline rate. **Block 9 is 32 typed
queries and exists solely to clear the other half** - previous sessions had essentially no
text sample, so the text bar was never gradeable at all rather than being failed.

`/palintel status` shows `⏳ n/30` per kind until each has enough.

Say every line with the wake word: *"Hey pal, …"*.

---

## Block 1 — Q1 resources (15)

The first four are the Phase 1 set and should all take the fast path (`/palintel recent`
shows `~0.1s fast`). The rest are resources that did not exist before Phase 2.

| # | Say | Expect |
|---|---|---|
| 1 | where's the nearest coal | Coal locations |
| 2 | where can I find ore | Ore locations |
| 3 | what's the closest sulfur deposit | Sulfur locations |
| 4 | show me quartz near my base | Quartz locations |
| 5 | where's the nearest stone | Stone locations |
| 6 | where can I find wood | Wood locations |
| 7 | where's the nearest paldium | **Paldium Fragment** locations |
| 8 | find me some hexolite quartz | **Hexolite Quartz**, not Quartz |
| 9 | where's the nearest chromite | Chromite locations |
| 10 | where can I find soralite | Soralite, tagged `sky island` |
| 11 | where's the nearest crude oil | "isn't a mineable node — it comes from oil rigs" |
| 12 | any sulfur worth mining nearby | Sulfur locations |
| 13 | can I get coal at this level | Coal locations |
| 14 | what's the best place to farm quartz | Quartz locations |
| 15 | I need coal for a new base | Coal locations |

**Watch for:** 5–10 are new nouns and **only the first five resources are in the STT
hotword list** — stone, wood and paldium are deliberately not hoisted. If they mis-hear
repeatedly, that is the measured trade-off showing up in practice, and it is the evidence
needed to revisit it. Note what you actually said and what `/palintel recent` shows it
heard.

## Block 2 — Q2 Pals (17)

| # | Say | Expect |
|---|---|---|
| 16 | where can I find Chillet | **two cards**: Chillet + Chillet Ignis |
| 17 | where do Foxparks spawn | two cards: Foxparks + Foxparks Cryst |
| 18 | where's the nearest Lamball | Lamball, lvl 1–3 |
| 19 | where can I find Anubis | lvl 68–72, far south-west (Feybreak) |
| 20 | where's the alpha Anubis | **(-134, -94), lvl 55, field alpha** |
| 21 | where can I find Depresso | night only |
| 22 | where do Cattiva spawn | lvl 1–5, starter area |
| 23 | where can I find Necromus | "The only Necromus out there is a field alpha" |
| 24 | where can I find Jetragon | alpha only |
| 25 | where can I find Bellanoir | "isn't found in the overworld" |
| 26 | where can I find Lifmunk | Lifmunk, lvl 4–6 |
| 27 | where can I find Leezpunk | Leezpunk + Leezpunk Ignis |
| 28 | where do Jormuntide spawn | lvl 80, `<1% of spawns here` |
| 29 | where can I find Mau | "isn't found in the overworld" (dungeon-only) |
| 30 | where's the alpha Chillet | Chillet alpha + "Chillet Ignis has no field alpha" |
| 31 | where can I find Vanwyrm Cryst | **two cards, incl. plain Vanwyrm** — see §Judgements |
| 32 | where do Grizzbolt spawn | lvl 70–80 |

**Watch for:** 26 and 27 are the pair the router has historically confused — "Leithbunk"
was answered as Lifmunk in the paid eval, the one genuinely wrong entity in 57. If 27
comes back as Lifmunk, that is the known failure reproducing in speech.

## Block 3 — follow-ups, spoken back to back (7)

Say each **immediately after** the one above it. Memory holds 4 turns for 5 minutes.

| # | Say | Expect |
|---|---|---|
| 33 | where can I find Anubis | normal spawns |
| 34 | what about the alpha? | Anubis alpha, (-134, -94) |
| 35 | where's the closest one? | still Anubis |
| 36 | and coal? | **switches to Coal** — not a Pal |
| 37 | what about quartz | Quartz |
| 38 | where can I find Depresso | night only |
| 39 | what about at night? | still Depresso |

**Then, without speaking:** type `what about the alpha?` into the Discord channel. It
should answer about **Depresso**, not ask you to restate — that is the cross-channel
promise `voice.speaker` exists to keep.

**Then wait 5+ minutes** (keep playing) and say *"what about the alpha?"* again. Expect
**"What was that about?"** asking for the name — not a guess.

## Block 4 — must NOT resolve (2, ungraded)

| # | Say | Expect |
|---|---|---|
| 40 | where can I find Chillet | Chillet |
| 41 | how about breeding Anubis | **a decline** — must not give a location |

A location card here is the failure conversation memory is priced at: an opener borrowing
the previous turn's verb.

## Block 5 — honest declines (3, ungraded)

| Say | Expect |
|---|---|
| how do I breed Anubis | decline, names what it can find |
| what should I research next | decline |
| where can I find adamantium | decline naming the unmatched token |

## Block 6 — drops, both directions (10)

New classes, and `item_source` is the one thing in this document that **no eval measures** -
the prompt set has no "who drops X" utterance in it. Every line here is its only test.

| # | Say | Expect |
|---|---|---|
| 42 | what does Lamball drop | **Wool**, Lamball Mutton, then an `__Alpha only__` block |
| 43 | what does Chillet drop | Ice Organ + Leather, then `__Alpha only__`, then `__Level 80+ only__` |
| 44 | what does Vanwyrm drop | Bone, Gold Coin 10%, Ruby 1% — most of it alpha |
| 45 | what do I get from Astralym and Mycora | **two cards**, one per Pal |
| 46 | who drops flame organs | Blazamut, Suzaku, Bushi Noct |
| 47 | what drops wool | Kingpaca, Kingpaca Cryst, Melpaca |
| 48 | where do I get leather | 122 sources, ordinary first |
| 49 | who drops ancient civilization parts | *"No ordinary encounter drops this"* then alphas |
| 50 | what drops paldium fragment | Lunaris |
| 51 | who drops bone | ordinary sources first |

**Watch for:** 46-51 name items that are **ordinary English words**. They are in the tool
enum but deliberately **not** in the lexicon, so nothing ranks them for the router - it
resolves them on sentence context alone. If an item query comes back with a *Pal* entity,
or a Pal query comes back with an item, that is the risk this design took, showing up.

**Watch also:** 43 and 49 are the level-band and alpha splits. A Chillet does **not** drop
30-50 Ancient Relics; only a level 80 one does. If the card ever shows that without the
heading, the conflation is back.

## Block 7 — the pictures (no new queries)

Read these off the cards blocks 1-6 already produced.

| Look at | Question |
|---|---|
| Any resource or Pal card | Does the map arrive, and how long after the text? |
| The map itself | Is the marker **on the thing**? This is the ground-truth check below, done visually. |
| The blue `you` dot | Findable at a glance, or lost against the terrain? |
| A card with 3 markers | Do the numbers match the text lines 1/2/3? |
| Kingpaca (`where can I find Kingpaca`) | *"Map shows MainMap only — #3 is on another map"* — is that clear or confusing? |
| Any Pal card | The icon in the corner: useful, or noise? |

**The one that decides the feature:** does the text card arriving first and the picture
appearing a moment later read as responsive, or as jank? That trade was chosen sight
unseen and `art_post` is its cost.

## Block 8 — ranch (3)

| # | Say | Expect |
|---|---|---|
| 52 | where can I find Lamball | spawn card + `Ranch: **Wool** _(unofficial)_` |
| 53 | where can I find Vixy | `Ranch:` three items + `_+4 more_` |
| 54 | where can I find Mau Cryst | `_(unofficial - the game files don't list this one as ranchable)_` |

**Watch for:** ranch facts are the only thing on a Tier 1 card not extracted from the game.
Does `(unofficial)` read as a useful caveat, or as noise on every ranchable Pal?

## Block 9 — the text pass (32, typed not spoken)

**This block exists because the phase debt cannot close without it.** The exit criterion
is p95 over **≥ 30 answered queries of each kind**, and every block above is spoken. The
best session so far reached 16 voice and effectively zero text, which is why latency has
been carried forward twice.

Type these into the channel. No wake word, no STT, so they are quick — five minutes of
typing is the whole thing, and text p50 has measured 0.3s, so this is about *sample size*
rather than about risk.

| # | Type | # | Type |
|---|---|---|---|
| 55 | where's the nearest coal | 71 | what does Lamball drop |
| 56 | where can I find ore | 72 | what does Chillet drop |
| 57 | closest sulfur deposit | 73 | who drops flame organs |
| 58 | show me quartz near my base | 74 | what drops wool |
| 59 | where's the nearest stone | 75 | where do I get leather |
| 60 | where can I find wood | 76 | who drops paldium fragment |
| 61 | where's the nearest paldium | 77 | where can I find Cattiva |
| 62 | find me hexolite quartz | 78 | where can I find Lifmunk |
| 63 | where's the nearest chromite | 79 | where do Foxparks spawn |
| 64 | where can I find soralite | 80 | where can I find Depresso |
| 65 | where can I find Chillet | 81 | where can I find Vixy |
| 66 | where do Lamball spawn | 82 | where can I find Jetragon |
| 67 | where can I find Anubis | 83 | where can I find Bellanoir |
| 68 | where's the alpha Anubis | 84 | what does Vanwyrm drop |
| 69 | where can I find Leezpunk | 85 | where's the nearest Lamball |
| 70 | where do Grizzbolt spawn | 86 | where can I find Kingpaca |

**Watch for:** these are deliberately the same questions as the spoken blocks. Anything
that succeeds typed and fails spoken is an STT or wake-word problem, not a routing one -
and that split is otherwise very hard to see from the outside.

`/palintel status` grades voice and text separately and shows `⏳ n/30` until each has
enough. Both need to clear it in **one** session; the activity log is memory-only and does
not survive a restart.

---

## Ground truth — read these off the in-game map

**Regenerated 2026-08-10.** The previous table's coordinates came from the pre-fix node
dataset and no longer exist: 16.4% of it was dungeon interiors, and coal went from 552
clusters to 308. Anything you remember checking before is worth checking again.

The whole spawn dataset (19,272 areas) still has **one** verified landmark, and **no
resource node has ever been stood on**. The map crops make this checkable at a glance for
the first time — the marker either sits on the rock or it does not.

From the save position **(229, -487)**:

| Ask | Card should say | Check |
|---|---|---|
| where's the nearest coal | (198, -231), 1 deposit, lvl 28+ | 308 clusters known, down from 552 |
| where can I find ore | (227, -481), 1 deposit, lvl 6+ | ~20 units away — walk to it |
| where's the nearest stone | (224, -483), **31 deposits** | 31 in one place: is it really one spot? |
| where can I find wood | (237, -484), 18 deposits | |
| where's the nearest paldium | (228, -490), 9 deposits | closest of the lot |
| what's the closest sulfur | (247, -256), 1 deposit, lvl 23+ | |
| show me quartz near my base | (-53, -960), 2 deposits | 570 units — the long one |
| where do Cattiva spawn | (214, -485), lvl 1–5 | are they actually there, at that level |
| where can I find Lifmunk | (197, -444), lvl 3–7 | |
| where's the nearest Lamball | (226, -485), lvl 1–3 | |

**The four nearest — ore, stone, wood, paldium — are all within ~25 map units (~115 m) of
where you spawn.** That makes them the cheapest possible test of the transform, and none
of them has ever been checked. If the marker is on the rock, the chain is confirmed at a
scale it has never been tested at; if it is 50 m out, every coordinate this project has
ever printed is 50 m out.

**Also worth one look:** stand at a `danger: low` node and a `danger: high` one and judge
whether the surrounding Pal levels match. The difficulty rule shipped **uncalibrated** —
`03-data-ingestion.md` §5 asks for ~20 nodes of known difficulty and it has had none.

**And the coverage question this fix created:** you can no longer ask for cave coal at all.
672 of 998 coal deposits were dungeon interiors and are now excluded. Is 308 overworld
clusters enough in practice, or is "find dungeons near me" the feature that has to follow?
That is the call the backlog entry is waiting on.

---

## Judgements only you can make

1. **Variant families.** #16 shows Chillet *and* Chillet Ignis. #31 shows Vanwyrm *and*
   Vanwyrm Cryst — **even though you named the variant explicitly**. Is the second card
   useful, or noise? These are arguably different cases and can be decided separately.
2. **PvP spawners.** Are `BP_PalSpawner_Sheets_PvP_*` live in normal play? They are
   excluded, which costs no coverage but suppresses 83% of Rushoar's spawn points. If you
   see Rushoar/Chikipi far more often than the cards suggest, the exclusion is wrong.
3. **Does `encounter_share` read as useful** or as clutter next to a coordinate?
4. **Alpha default.** "Where can I find X" returns normal spawns and never mentions that
   an alpha exists. Should it?
5. **`maps` and `icons` are one flag pair but two features.** Icons are cheap and carry no
   failure modes; maps carry all of them. Keeping one and dropping the other is a real
   option.
6. **Card density.** Resource cards now carry "Also drops from" and Pal cards carry
   "Ranch:". A1 retired density as a *constraint*; whether these earn their lines is an
   editorial call and it is yours.
7. **The minimum crop is 200 map units (~920 m).** Too tight and the picture is a blur,
   too wide and the markers shrink. Arbitrary until you have looked at a few.

---

## Afterwards

- `/palintel status` — the graded p50/p95 for voice and text, the stage breakdown, and
  the new **`artwork (after the answer)`** line: `render` is local CPU and known
  (~8 ms p50, 25 ms p95); **`post` is the number this whole session exists to produce.**
  It is deliberately outside the graded kinds, so it cannot flatter or spoil the voice
  and text figures.
- `/palintel recent` — the last 12 queries with routing time; `~0.1s` is the fast path,
  seconds mean the model
- Anything mis-heard: the verbatim transcript is worth more than the fact that it missed

Note the numbers before restarting the bot — the activity log is memory-only and does not
survive it.
