# Play session protocol — Phase 2

A scripted session, because three things are now open that only real play can close: the
end-to-end latency criterion (unmeasured across two phases), the spawn dataset's
correctness against an actual map, and several design calls that a harness cannot judge.

**The script is not the point; the misses are.** Phase 1's most useful finding came from
reading verbatim transcripts of queries nobody would have thought to write down. Run the
script, then keep playing and ask whatever you actually want — `/palintel recent` is what
makes the unscripted half diagnosable.

---

## Before you start

1. **Set the speaker.** In `config.local.toml`:
   ```toml
   [voice]
   speaker = "<your Discord display name>"
   ```
   Without it, spoken questions and typed follow-ups land in separate memory threads and
   block 4 below fails for the wrong reason.

2. **Check `/palintel status`.** It should report the mic device, `hey_pal @ 0.1`, and
   `heard as <your name>`. If it says `unattributed`, step 1 did not take.

3. **Expect the latency picture to look worse than Phase 1's best session.** The fast path
   now carries ~61% of a two-class mix rather than 78%, because Q2 queries it cannot claim
   go to the model. That is predicted, not a regression.

---

## What the numbers need

The criterion is **p95 ≤ 2.5s over ≥ 30 answered voice queries**, and declines are tracked
but not graded. The script below has **41 gradeable queries** so the count survives a
normal decline rate; `/palintel status` shows `⏳ n/30` until it has enough to grade.

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

---

## Ground truth — read these off the in-game map

The whole spawn dataset (19,272 areas) has **one** verified landmark. The resource ingest
had ~20 before it was trusted. Please check as many of these as you pass near; a miss of
more than ~10 map units is a real problem, not a rounding one.

| Ask | Card should say | Check |
|---|---|---|
| where's the alpha Anubis | (-134, -94) lvl 55 | the known-good one; confirms the chain |
| where's the nearest coal | (321, 500), 9 deposits | are there really ~9 coal rocks there |
| where can I find ore | (-74, -316), 11 deposits | |
| where's the nearest stone | (124, -401), 40 deposits | 40 is a lot — is it one place? |
| where can I find wood | (137, -444), 34 deposits | |
| where's the nearest paldium | (-622, 268), 16 deposits | |
| where do Cattiva spawn | (214, -485), lvl 1–5 | are they actually there, at that level |
| where can I find Lifmunk | (-304, -15), lvl 4–6 | |

**Also worth one look:** stand at a `danger: low` node and a `danger: high` one and judge
whether the surrounding Pal levels match. The difficulty rule shipped **uncalibrated** —
`03-data-ingestion.md` §5 asks for ~20 nodes of known difficulty and it has had none.

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

---

## Afterwards

- `/palintel status` — the graded p50/p95 for voice and text, and the stage breakdown
- `/palintel recent` — the last 12 queries with routing time; `~0.1s` is the fast path,
  seconds mean the model
- Anything mis-heard: the verbatim transcript is worth more than the fact that it missed

Note the numbers before restarting the bot — the activity log is memory-only and does not
survive it.
