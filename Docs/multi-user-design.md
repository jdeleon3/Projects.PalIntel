# Multi-user PalIntel — discovery and design

*Written 2026-08-13, the day after Discord voice receive was restored and 13 spoken
questions were answered from a voice channel, each attributed to the member who spoke.
That is what makes this question live: **the input path is already multi-user and the
answer path is not.***

*Revised the same day against a **real two-player world** —
`44403D774601FB7B22EA0C83E1A16FE5`, players `Rui` and `OutofLuck`, guild `Foobar`. The
first draft said its central claims could not be tested on this machine. They could, they
now are, and §2.6 is what the co-op save said. One of them came back **refuted**.*

---

## 1. The short version

The bot is much closer to multi-user than it looks, and the one thing you named — player
data — splits into three parts with very different costs:

| Player data | Where it lives | Cost to make per-person |
|---|---|---|
| Position, technologies, tech points, ancient points, towers beaten | `Players/<PlayerUId>.sav`, **already one file per player** | **Small.** Read every file instead of the newest one. No new parsing. |
| Owned Pals | `Level.sav`, one shared table | **Medium, and it has a trap** — see §4 |
| Base camps | `Level.sav`, owned by the **guild**, not by a player | **Medium.** Correct answer is guild-scoped, not player-scoped |
| Player level | Nowhere readable | Unchanged. Still `None`, still behind the stale `Level.sav` decoders |

The genuinely hard part is not parsing. It is that **multi-user introduces a new way to
be confidently wrong**: answering Alice's question with Bob's coordinates. The card is
well-formed, the numbers are real, and nothing on it is marked wrong. That is the exact
failure this project refuses to ship, and §6 is the invariant that guards it.

There is also **a bug to fix before any of this, not during it** — §7.

**And one thing gates all of it, which the first draft of this document got wrong.** The
question is not how many people are in the world; it is **whether the save is on the
machine running the bot**. When a friend hosts and you join, this machine holds one file —
`LocalData.sav` — and it carries no player state whatsoever. That is measured, in §4.1.1,
against the group's most recent multiplayer world. **Everything below applies to a world
this machine can read. Read §4.1 before scheduling any of it.**

---

## 2. What was measured

Everything in this section was read off the live save at
`.../DD98A01E404940546C3C66A397203F80` on 2026-08-13, not inferred from documentation.
Probes are in the scratchpad; each claim below names how it was checked.

### The save states who owns what — and the join is exact

`CharacterSaveParameterMap` holds 559 entries. **516 of them carry `OwnerPlayerUId`** in
the undecoded `RawData` blob, readable by the same parse-don't-pattern-match technique
`_character_id` already uses.

The Guid is stored as UE's `FGuid` — four little-endian `uint32` — so the raw bytes read
`00000000 00000000 00000000 01000000` where the player save's `PlayerUId` string is
`00000000-0000-0000-0000-000000000001`. Once byte order is handled **the join is exact**,
not a heuristic. That matters: this project has a standing note that a `BOSS_` prefix
meaning "the alpha of" is an *inference*. This is not one. The save states it.

`Players/` holds one `.sav` per player, keyed by that same `PlayerUId`, at ~13 KB each.
So position, `UnlockedRecipeTechnologyNames`, `TechnologyPoint`, `bossTechnologyPoint` and
`TowerBossDefeatFlag` are **already per-player and already parsed** by `read_player`. The
only reason they are single-user today is `newest_player_save()`, which takes the most
recently written file and throws the rest away.

### The save names its players

The entry with `IsPlayer` present carries `NickName` — `'Rui'` on this save — and its map
key is that player's `PlayerUId`. So identity binding can be a name the player recognises
rather than a Guid nobody can type. The guild's raw data carries the member list too:
readable strings `'Unnamed Guild'` and `'Rui'` sit in its tail.

### The naive owner filter is wrong, and I measured how wrong

This is the finding that changes the design.

| | Distinct species |
|---|---|
| `owned_species` today (union of every character in the world) | **195** |
| Filtered to `OwnerPlayerUId == the player` | **184** |
| **Silently lost** | **11** |

The 11: `baphomet_dark`, `boss_anubis`, `boss_captainpenguin`, `boss_littlebriarrose`,
`boss_volcanicmonster`, `boss_winggolem`, `ghostrabbit_grass`, `mysterymask`,
`scorpionman`, `swordcutlassfish`, `whitemoth`. Six of them are ordinary Pals that
`counters.py` would happily shortlist.

**43 entries carry no `OwnerPlayerUId` at all, and every one of them has `SlotIndex`** —
they are in a container, which is to say assigned to a base camp or sitting in the Palbox.
They are the player's Pals. They are just not *carried* by the player, so the game does
not stamp a personal owner on them; they belong to the guild.

This is the project's named failure mode arriving on schedule. The filter would work
perfectly, produce a well-formed roster, join cleanly, pass every type check — and quietly
shrink your roster by 5.6% **on a save with exactly one player in it**, where multi-user is
not even involved. Nothing would look wrong. `/palintel status` would say
"checked 184 of your Pals" and the number would be plausible.

### Base camps and base Pals belong to the guild

`GroupSaveDataMap` holds 8 groups, one of type `EPalGroupType::Guild`. Its 18 KB raw blob
opens with the group id and a handle list of **559 `(PlayerUId, InstanceId)` pairs — exactly
the 559 characters in `CharacterSaveParameterMap`** — and carries the base ids, guild name
and member list after it.

So the sharing boundary in Palworld is the guild, and it cuts across the three base camps
in `BaseCampSaveData` (which carry no owner field of their own — that join only exists
through the guild).

*Caveat, stated because I got it wrong once already in this session:* my first probe
printed "this save has no Guild group", which was false — the group type counter found one.
And my guess at the raw layout mis-read the handle list as a base-id list. **The guild blob
needs a real structural decoder, not the offsets in this document.** What is established is
that the data is there and readable; the exact field boundaries are not yet established.

### 2.6 The two-player world — what the co-op save actually said

World `44403D774601FB7B22EA0C83E1A16FE5`, two players, one guild. This is the test the
first draft of this document said it could not run.

**Per-player state diverges, and Q6 is where it diverges hardest.**

| | `Rui` | `OutofLuck` |
|---|---|---|
| `PlayerUId` | `00000000-…-0001` | `48f23c66-0000-…-0000` |
| Position | (228, −485) | (230, −486) |
| **Technologies unlocked** | **35** | **61** |
| **Points / ancient points** | **83 / 7** | **59 / 8** |
| Towers beaten | `GrassBoss` | `GrassBoss` |

**This is a live cross-attribution failure, not a hypothetical one.** `newest_player_save()`
returns whichever file the game wrote last, so today *"what should I research next"* asked
by either player is answered against a 35-technology tree or a 61-technology one depending
on save timing — and *"what should I spend my ancient points on"* is answered against the
wrong budget, 83/7 against 59/8. Both cards would be entirely well-formed.

One weakness in this reading: the two players were standing **two map units apart** when
the world was last saved, so this save is a poor test of *position* divergence. Technology
divergence is the strong evidence here; proximity is still untested.

**Both players are named**, so identity binding by nickname holds: `Rui` and `OutofLuck`,
each joining its `PlayerUId`.

**`OwnerPlayerUId` separates the two rosters cleanly.**

| | Species |
|---|---|
| `owned_species` today (the union — what every card sees) | **53** |
| Attributed to `OutofLuck` | 39 |
| Attributed to `Rui` | 32 |
| Held only by `OutofLuck` | 20 |
| Held only by `Rui` | 13 |
| Held by both | 19 |
| No owner (container/base Pals) | 11 entries, 8 species, **1 species found nowhere else** |

So today a counter card tells `Rui` it *"checked 53 of your Pals"* when 32 are his — a **66%
over-count** — and will happily shortlist `fairydragon` or `wizardowl` because the *other
player* owns one. That is the failure this whole design exists to prevent, and it is
reachable today by two people in a co-op world asking the same question.

**And the obvious field is the wrong field — the refuted claim.** The map key of
`CharacterSaveParameterMap` also carries a `PlayerUId`, and it is the one you reach for
first. Its distribution here is **239 `Rui` to 1 `OutofLuck`**. It is not ownership; it
appears to be the world/host key. Reading it would produce a clean, plausible, total
misattribution — "OutofLuck owns one Pal" — with no error anywhere. Only the blob's
`OwnerPlayerUId` is the owner. *The design already used the blob field, so this reinforces
it; it is recorded because the wrong field is the more obvious one and the next person to
open this file will reach for it.*

**Bases are guild-scoped, confirmed.** One `BaseCampSaveData` camp, one
`EPalGroupType::Guild` group whose raw blob carries the guild name `Foobar` and **both**
member nicknames. The single base is shared by both players, so *"rate my base"* is a guild
question and not a personal one.

**But the guild handle list is keyed by the map key, not the owner.** Searching the guild's
7,953-byte blob for each player's UID finds `Rui` 241 times and `OutofLuck` 3 — which
tracks the 239/1 map-key split, not the 32/39 ownership split. So **§4.4's guild-container
join cannot be taken from the guild's handle list.** It has to go through
`CharacterContainerSaveData` → slot `InstanceId` → the character entry. That is a real
correction to the design, and it is the part M3 has to build carefully.

**One number that shrank.** The naive owner filter lost 11 species on the single-player
save and only 1 here. The difference is base infrastructure — three base camps there
against one here — so **the size of the trap grows over a playthrough**, and measuring it
on a young world understates it. The single-player save is the worst case measured so far,
not the outlier.

---

## 3. What is already multi-user (more than expected)

Worth stating plainly, because it is most of the work and it is done:

- **`Pipeline.handle(utterance, state, who)`** takes both the asker and their state as
  arguments. There is no global player state inside the pipeline. Every class already
  reads `state.owned_species`, `state.player_coords`, `state.tech`, `state.base_camps`
  from the object it was handed.
- **`Memory` is already keyed per user** with a TTL, and `/palintel reset` is already
  scoped to the asker — with a comment explaining that a global reset would let one person
  disrupt everyone else.
- **Discord voice receive already attributes each packet to the member who sent it.**
  This is the capability that makes the rest worth building.
- **`spend.Charge` already carries `who`.**
- **`capture.record_feedback` already carries `who`.**
- `KnowledgeBase`, the router, cards, corpus and artwork are read-only and shared. Nothing
  there needs to change at all.

The single-user assumption is concentrated in one place: **`bot._answer` builds one
`PlayerState` from one `SaveWatcher`, and ignores `who` when it does.**

That is the whole defect, and it is eight lines.

---

## 4. Design

### 4.1 Two axes, not two products — *this section was wrong in the first draft*

The first draft framed the choice as "several people in **one world**" versus "several
people in **separate worlds**", and treated the first as the small case. That framing does
not survive contact with how this group actually plays, and following it would have built
M1 and M2 and then discovered they do not help the most common setup.

There are two independent axes:

| | |
|---|---|
| **Who is asking** | How many Discord users the bot serves. **Already solved** by the voice work — every packet names its member |
| **Where the world lives** | Whether the save is on the machine running the bot. **This is the one that decides what is possible at all** |

The second axis has nothing to do with how many people are playing:

| Setup | People | Save reachable? | What works |
|---|---|---|---|
| Solo | 1 | ✅ yes | Everything (today) |
| **Co-op, you host** | 2+ | ✅ yes | Everything, once M1–M3 land |
| **Co-op, a friend hosts** | 2+ | ❌ **no** | §4.1.1 — and this is the group's most recent world |
| Dedicated server | 2+ | ✅ only if the bot runs on the server host | Everything, once M1–M3 land |

A world with several people in it can be completely unreachable, and a world with one
person in it is reachable. **Reachability is the constraint; head count is not.**

### 4.1.1 When a friend hosts, this machine has nothing — measured

World `8C0191774C5A57C19236AB9035A65DC7`, played 2026-08-02, the group's **most recent**
multiplayer session. The local directory holds exactly one file: `LocalData.sav`.

No `Level.sav`. No `Players/`. Scanning `LocalData.sav`'s 5.2 MB of decompressed bytes for
every field the bot reads finds **none of them** — no `LastTransform`,
`UnlockedRecipeTechnologyNames`, `TechnologyPoint`, `bossTechnologyPoint`,
`TowerBossDefeatFlag`, `CharacterID`, `OwnerPlayerUId` or `NickName`. It is a static
catalog of item and Pal ids, near-identical in size and content across all three worlds on
this disk. It is not player state and it is not world state.

It also does not parse with `PALWORLD_TYPE_HINTS` — the same stale-decoder family as
`Level.sav`'s blobs — but that is beside the point, because a byte scan already shows there
is nothing in it worth decoding.

**So when a friend hosts, `PlayerState` is empty and no amount of work in this repo changes
that.** The state exists; it is on their disk.

What that costs, by class:

| | Classes |
|---|---|
| **Unaffected** — need no player state | Pal drops, item source, `pal_info`, Pal search by attribute, base criteria, Q7 mechanics, **and a base rating from a stated coordinate** |
| **Degrade, and already say so** | Resource location and Pal location (rank by cluster size, no `Nearest:` row), counters (*"I haven't read your Pals"*), mount search (no unowned filter), base siting (no proximity) |
| **Declines outright** | *"What should I research next"* — set arithmetic over your unlocked technologies and two point pools, and with none of them there is no answer. The named-technology lookup survives in weakened form: every gate renders ❔ rather than ✅/❌ |

Roughly 6 of the 13 production classes work fully, 5 degrade to behaviour they already
implement and announce, and 1½ stop being useful. **That is a real product, not a broken
one** — and it is what a joined-world player gets today whether or not anything here is
built.

**Worth checking rather than assuming, and it holds.** `PlayerTech.unlocked = None` is a
different value from the empty set all the way to the card, so with no save `progression_card`
returns *"I haven't read your save — I can't tell you what to research next without seeing
which technologies you already have"*, in decline colour. It does **not** fall through to a
level floor of zero and recommend tier-1 technologies to a level-57 player, which is what an
empty-set reading would have produced: a shopping list that is well-formed, plausible and
wrong. The `None`-means-not-read discipline that `owned_species` established is already
carrying this class, and the friend-hosted case is the first thing to actually exercise it.

### 4.1.2 Options for the friend-hosted case

**The requirement is not "the bot runs on the host's PC". It is "the bot's process can
`open()` the save directory".** That distinction is worth holding onto, because it admits a
cheaper option than relocating the bot.

| | Cost | Verdict |
|---|---|---|
| **Read the host's save over a file share** — they share the save directory read-only, `save_dir` points at a UNC path | **A config value.** `Config.save_dir` is already a `Path` and nothing resolves or normalises it; `SaveWatcher`, `newest_player_save` and `_build_watcher` only glob, `stat` and read | **Try this first.** No new component, no ADR, no code change, and the bot stays on your machine with your GPU and your `data/`. Needs the two machines on one LAN or a VPN, and the host has to share a folder. *Untested against a real share* — the code path is ordinary file IO, but nobody has run it |
| **Run the bot on the host's PC** | The host needs the repo, the ingested `data/`, and a GPU for STT | Works, and is the fallback if the share is impractical. With Discord voice receive the bot is headless and everyone talks in the voice channel — including you, from your machine. The joining player's `Players/<uid>.sav` is on the host, so M1–M3 cover *everyone* |
| **Move to a dedicated server** | Server hosting; bot runs alongside it, or reads its save over a share | Same as either row above with nobody's gaming PC as the dependency. Best if the group plays often |
| **A read-only agent on the host** shipping `PlayerState` to the bot | A new networked component, its own failure modes, and a new ADR against ADR-0003 | Only if none of the above is acceptable. The file share is this idea with the protocol already written by the operating system |
| **Manual declaration** (`/palintel iam level 42`) | Small | ADR-0005 already retains this as the A2-failure fallback. It requires typing — the thing the project exists to avoid — and drifts silently out of sync. A last resort |
| **Accept the degradation** | Zero | The honest default, and what happens anyway. The cards already know how to say they have not looked |

### 4.1.3 Cloud sync (Google Drive / Dropbox) — works for three of the four state kinds

Proposed 2026-08-13: the host syncs their save folder to Drive, shares it, and the bot
reads the synced copy locally. Structurally it works — it is a local path in the end — and
**it decomposes better than expected, because the four kinds of player state move at wildly
different rates.**

| State | Changes | Already polled at | Survives a sync delay of minutes? |
|---|---|---|---|
| `player_coords` | every second | 20 s | ❌ **No.** This is what "nearest" resolves against |
| `owned_species` | when you catch something | 300 s | ✅ Yes |
| `tech` — unlocked, both point pools | when you research | 300 s | ✅ Yes |
| `base_camps` | rarely | 300 s | ✅ Yes |

`SaveWatcher` already polls position and the roster on separate cadences, for cost reasons.
**The same split is the right one for staleness**, which is a pleasant accident rather than
a design anticipating this.

So cloud sync restores **Q5 counters, Q6 research and base rating** — including the class
that dies completely in the friend-hosted case, since technology state is exactly the
slow-moving kind sync handles well — and does **not** restore trustworthy proximity.

**What it costs the host.** `Level.sav` is 1.5 MB and is rewritten whole on every autosave;
Drive does no block-level delta for binaries, so a long session is hundreds of full
re-uploads. Exclude `backup/` from the sync or it accumulates — the co-op world on this disk
has 17 snapshots of ~5 files each.

**What is already safe.** ADR-0005's read-only invariant means the bot can never write to
the save directory, so no sync configuration can corrupt the host's game. And a file caught
mid-write fails cleanly: `decompress` checks the header's length fields against the body,
`poll` keeps the previous snapshot and leaves `_mtime` alone so the next tick retries.

**A VPN file share is strictly better on every axis that matters here.** Tailscale or
similar gives a real path over the internet with no port forwarding, no upload churn and
near-zero staleness. The usual objection — "it needs both machines online at once" — is
vacuous in this case: **you are playing together, so both machines are online by
definition.** Cloud sync's one advantage is asynchrony, and asynchrony is worth nothing for
a question about where you are standing right now.

### 4.1.4 The staleness gate — a latent defect, and sync makes it routine

**There is no save-age check anywhere in the codebase.** `PlayerSnapshot.read_at` is when
*this process* read the file, not when the game wrote it. `_mtime` is captured in `poll()`
purely to detect change and is never stored on the snapshot or tested for age.
`player_coords()` returns the position unconditionally.

So today, with the game closed, the bot answers *"where's the nearest coal"* against
whatever position the save last held — and `describe()` reports *"read 3s ago"*, which is
**reassuring and about the wrong clock**. A coordinate card computed from a week-old
position is byte-for-byte identical to one computed from a live one. That is this project's
signature failure, sitting in the one class that sends the player somewhere, and the
2026-08-12 session already produced a card that got the player killed by naming a real place.

Sync does not create this. It makes it the common case.

**The fix is small and worth doing regardless of any multi-user work.** `st_mtime` is
preserved across Drive and SMB alike, so it is the host's *game* write time even on a synced
copy — the bot can know the true age and simply does not look.

- `PlayerSnapshot` carries `written_at` from the file's mtime alongside `read_at`.
- Past a threshold, `player_coords()` returns `None`. Every card already handles that: it
  ranks by cluster size and says so. The honest path exists and is unwired.
- Below the threshold but not fresh, **print the age on the card** rather than gate silently.
  Making the uncertainty visible beats withholding a usable answer.
- The threshold wants measuring from real autosave intervals rather than picking a number.
  Nobody has recorded Palworld's write cadence during play; the mtimes of one session would
  settle it.

Two things a share changes that are worth measuring rather than assuming:

- **Poll cost.** `Level.sav` is 1.5 MB and the roster poll reads it every 5 minutes; the
  player saves are ~13 KB every 20 s. Trivial locally, and it becomes network IO on a
  timer. It already runs in an executor, so a slow read delays a roster refresh rather than
  the loop — but nobody has timed it over SMB.
- **Reading a file mid-write** is *more* likely across a share, not less. `decompress`
  already checks the header's length fields against the body precisely so a torn read
  raises rather than reaching the GVAS parser, and `poll` leaves `_mtime` alone on failure
  so the next tick retries. The existing degradation path covers this; it has just never
  had to.

**Recommendation: try the file share, fall back to running the bot on the host, and accept
the degradation if neither is convenient.** Do not build the agent until someone has played
a session with the degradation and said it is not enough.

### 4.2 The core change: `PlayerState` per asker

```
SaveWatcher                      -> SaveWatcher
  .snapshot: PlayerSnapshot|None      .snapshots: dict[uid, PlayerSnapshot]
  .roster: frozenset|None             .rosters: dict[uid, Roster]
  .base_camps: list|None              .guilds: dict[guild_id, Guild]
                                      .players: dict[uid, str]   # uid -> NickName
```

`bot._answer` gains one lookup:

```python
uid = identity.resolve(who)          # None when this speaker is unbound
state = watcher.state_for(uid)       # world-scoped PlayerState when uid is None
```

Everything downstream is unchanged. That is the payoff of `PlayerState` having been an
argument since Phase 0.

### 4.3 Identity: bind Discord user -> PlayerUId

- **`/palintel who`** lists the players the save knows, by `NickName`, and which Discord
  users are bound to them. Discoverable, so nobody types a Guid.
- **`/palintel iam <nickname>`** binds the asker.
- Persisted to `data/players.json` (gitignored, like everything else in `data/`).
  Deliberately **not** written into `config.local.toml` — the bot should not rewrite its
  own config file — and deliberately **not** in-process only, because rebinding four
  people after every restart is how a feature stops being used.

  This is a mapping, not player state, so it does not touch ADR-0005's "`PlayerState` is
  never persisted" constraint. Worth stating in the ADR rather than left to reading.
- **Key conversation memory on the Discord user id, not `display_name`.** Today `who` is
  a display name. Display names change mid-session and collide across servers, and either
  one silently splits or merges someone's conversation memory. Keep the display name for
  presentation; key on the id.

### 4.4 The roster, done correctly

Not one set. Two sources, unioned:

```
owned(uid) = { species where OwnerPlayerUId == uid }        # carried by this player
           | { species in this player's guild's containers } # base + Palbox, shared
```

This is not a special case bolted on to make the number come out right — it is what the
game means. Base Pals genuinely are usable by any guild member; that is what a guild is.
The co-op save bears this out: one base camp, one guild, both players in it.

And it has a property worth having: **in a single-player world it returns exactly today's
195**, because one player's guild is all of it. So the change is *behaviour-preserving on
the current save*, which makes it testable against a number that already exists rather than
against a judgement.

**The second term is the expensive one, and §2.6 corrected how to compute it.** The guild
group's handle list is keyed by the map-key `PlayerUId` (239/1 on the co-op save), not by
the owner, so it cannot tell you whose containers are whose. The join has to run
`CharacterContainerSaveData` → slot `InstanceId` → the `CharacterSaveParameterMap` entry
with that `InstanceId`. `CharacterContainerSaveData` carries `Slots` and `SlotNum` in
readable properties, so this needs no new blob archaeology — but it is a different path
from the one this document originally proposed, and it is why M3 is its own phase.

`base_camps` gets the same treatment: guild-scoped, so "rate my base" in a co-op world
rates a base you actually have access to.

### 4.5 What each query class does with a per-player state

| Class | Scope | Note |
|---|---|---|
| Resource / Pal / mount / attribute search, `pal_info`, item source | **Per-player** via `player_coords` | "Nearest" already means "nearest to the asker". This is the class where cross-attribution is most visible and most dangerous — see §6 |
| Counters (Q5) | **Per-player + guild** | Uses the roster above |
| Technology (Q6, named lookup) | **Per-player, cleanly** | Each player has their own tech tree and their own two point pools, in their own file. This one is genuinely free |
| Base siting / rating (Q4) | **Guild** | Bases are shared. "My base" means the guild's |
| Base criteria, corpus (Q7), mechanics | **World** | Need no player state at all. Already correct |

---

## 5. What multi-user costs that is not parsing

- **Money.** $0.0048/request, and a party of four asking freely is four times the burn on
  one prepaid balance. `spend.Charge.who` already exists, so per-user totals are nearly
  free; whether anyone gets a *cap* is a decision, not a default. Note the balance warning
  is the only thing standing between a party session and the 429-storm the roadmap records
  being misread as a 13-point router regression.
- **Privacy, and it is sharper than the existing note.** STATUS already records that
  capture "records whatever is near the microphone, including other people in the room. It
  did." With Discord receive, capture records **other people's voices from their own
  machines** into your local corpus. That deserves an explicit in-channel announcement when
  capture is on, not a config comment. `capture.Utterance` also has no `who` field, so a
  party session's clips currently land in one corpus with no speaker attribution at all.
- **Concurrency.** Two people asking at once is now routine rather than incidental — which
  is §7.
- **Latency.** `p95` is already failing at 6.2s against a 2.5s budget, and it is measured
  as one population. Per-user it may look very different, and `activity.py` has no `who`
  dimension. It also still writes nothing to disk, so this session's numbers exist only in
  a pasted status line.

---

## 6. The invariant this needs

Multi-user's characteristic failure is **cross-attribution**: Alice asks where the nearest
coal is and gets the answer for where Bob is standing. Every number on that card is real.
Nothing is malformed. She walks 800 units the wrong way — and this project already has one
card that got the player killed by sending them somewhere real and wrong.

The rule, and it is the same rule `owned_species = None` already follows:

> **An unbound speaker gets world-scoped answers and is told so. Never another player's
> state.**

Concretely: `resolve(who) -> None` must produce a `PlayerState` with `player_coords=None`,
`owned_species=None`, `tech=None`, `base_camps=None` — not a fallback to "the newest save"
or "the host's". Those cards already know how to say they have not looked: *"I haven't read
your Pals"*, *"nearest is ranked by cluster size"*. They will say it. **The dangerous
implementation is the convenient one** — defaulting an unknown speaker to the host's state
makes the feature work beautifully for the host and silently lie to everyone else.

This is also why `newest_player_save()` should stop being used for state once this lands.
Today it means "the local player". In a co-op world it means "whoever the game wrote most
recently", which is a different person every few minutes.

---

## 7. Fix this first, separately

**`last_usage` is instance state on a shared router, read out-of-band after the executor
returns — and it is already wrong under concurrency today.**

```python
outcome = await loop.run_in_executor(None, pipe.handle, text, state, who)   # bot.py:288
...
usage = getattr(pipe.router, "last_usage", None)                            # bot.py:313
answered_by = "model" if usage is not None else "fast"
```

The default executor has multiple workers. Two overlapping queries interleave: A routes
through the model and sets `self.last_usage`; B enters `route()`, which clears it
(`routing_gemini.py:309`); A then reads `pipe.router.last_usage`, sees `None`, and is
logged as a **fast-path answer costing $0**.

This is the 2026-08-12 ledger bug returning by a different mechanism — the same two
figures wrong in the same direction that drains a prepaid balance early, and the same
capture log polluted with a wrong `path` label. It is reachable today whenever text and
voice overlap. Multi-user makes it the normal case.

**The fix is to return usage from the call rather than read it off the router afterwards.**
Small, independently testable, and it should land on its own commit before any of §4 — not
folded into it, or the multi-user work will get blamed for a ledger that was already
drifting.

Two smaller ones in the same family:

- `Memory._by_user` has no lock, unlike `ActivityLog`, and `recent()` mutates the deque it
  iterates. Cheap to fix; currently a narrow race, shortly a routine one.
- `_FeedbackButton` records feedback from **whoever clicks**, on anyone's card. Probably
  fine — a party correcting each other's cards is useful — but it should be a decision,
  and `who` is already recorded either way.

---

## 8. Phasing

Each phase is independently useful and independently shippable.

**All of M1–M3 assume a reachable save (§4.1).** On a friend-hosted world they change
nothing, because there is nothing to read. M0 and M4 apply either way.

**Phase M0 — the concurrency fixes.** §7. Prerequisite, not part of the feature.

**Phase M1 — per-player position and technology.** `SaveWatcher` reads all of `Players/*.sav`;
identity binding via `/palintel who` and `/palintel iam`; unbound speakers get world-scoped
state and are told so. **This alone makes Q1, Q2, Q6, mount search and base rating correct
for a party**, and it needs no new parsing at all — `read_player` already returns everything.

*Exit criterion, and the co-op save makes it concrete:* asked *"what should I research
next"*, `Rui` is answered against **35** technologies and **83/7** points and `OutofLuck`
against **61** and **59/8** — whichever order they ask in, and whichever save the game
wrote last. An unbound speaker gets a card that says it does not know who they are. This is
a regression test that can be written today, against a save already on disk.

**Phase M2 — the roster, per player and per guild.** `OwnerPlayerUId` extraction, guild
container membership, the union in §4.4.

*Exit criteria, both now real numbers:* on the single-player save the roster still reads
**195** — any other number is the §2 trap having caught us. On the co-op save `Rui` reads
**32 + shared** and `OutofLuck` **39 + shared**, never 53, and never each other's.

**Phase M3 — guild-scoped base camps and containers.** Both halves of the correction in
§2.6: the container join runs through `CharacterContainerSaveData`, not the guild handle
list, and the guild blob's own field boundaries are still not established.

**Phase M4 — the multi-user costs.** Per-user spend totals, capture attribution and the
consent announcement, per-user latency.

---

## 9. What is now verified, and what still is not

The first draft listed four things it could not check and asked for a co-op session to
settle them. A two-player save was already on disk. Three are settled; the fourth is not,
and two new gaps opened.

**Settled by the co-op save (§2.6):**

- ✅ **`OwnerPlayerUId` tells two players apart** — 32 / 39 / 19 shared, against a union of
  53. Not a weak test any more.
- ✅ **`newest_player_save` picking the wrong person** is confirmed as a real failure, with
  the sharpest evidence on Q6: 35 technologies against 61, and two different point budgets.
- ✅ **Identity binding by nickname** — both players named in the save.
- ✅ **Bases are guild-scoped** — one camp, one guild, both members in it.

**Still not verified:**

- ❌ **Position divergence.** The two players were standing two map units apart at the last
  save, so *"where's the nearest coal"* would have returned the same answer for both. The
  Q6 evidence carries the argument; the proximity half of it is still untested, and
  proximity is the class where a wrong answer sends someone somewhere.
- ❌ **The guild blob's field boundaries** — §2's caveat, unchanged. M3 depends on it.
- ❌ **Two people asking at once.** Never happened. STATUS lists it as an untested
  assumption for `SpeakerStream`; it is also untested for the spend ledger (§7),
  conversation memory and the router.
- ❌ **Whether the container join actually recovers the unowned Pals.** §2.6 refuted the
  path this document first proposed. The replacement is stated and unbuilt.

**One thing the two saves disagree about, and it matters.** The naive owner filter loses 11
species on the single-player world and 1 on the co-op world. The difference is base
infrastructure — three camps against one. So a young world understates the trap, and
**neither save is evidence about a mature co-op world**, which is the actual target. Do not
read the co-op save's "only 1 species lost" as the number to design against.

---

## 10. Decisions this needs from you

| | |
|---|---|
| **Where will the world live?** — *the one that gates everything* | The group's two multiplayer worlds sit on opposite sides of this. The Jul 11 world you hosted is fully readable and M1–M3 make it work. The **Aug 2 world your friend hosts is unreachable**, and it is the more recent one. Options and a recommendation in §4.1.2; the recommendation is **run the bot on whichever machine holds the world**, and accept the degradation otherwise |
| **Is the degradation acceptable?** | §4.1.1 measures it: 6 of 13 classes unaffected, 5 degrade to behaviour they already announce, Q6 stops working. Worth one session of finding out before building anything to avoid it |
| **Does an unbound speaker get answered at all?** | The design says yes, world-scoped, and says so on the card. The alternative is refusing until they bind, which is safer and ruder. The decline-policy rebalance of 2026-08-11 leaned toward answering; this is that trade again |
| **Is a caught alpha a party member?** | Already open in STATUS for the roster count (14 species held only as `BOSS_`). Multi-user does not create it, but M2 touches exactly this code and it would be cheap to settle while there |
| **Per-user spend cap?** | Four people on one prepaid balance. `who` is already logged; a cap is a policy, not plumbing |
| **Capture consent.** | If capture stays on with Discord voice, the bot is recording party members' voices. Announce in channel, require an opt-in, or keep capture mic-only? |
| **Whose feedback buttons?** | Anyone can currently label anyone's card |
