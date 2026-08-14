# PalIntel

A Discord bot that answers Palworld questions by voice or text while you play — with one
rule that shapes everything else: **coordinates, stats, and breeding pairs never
originate from a language model.** A card that's confidently wrong is the one failure
this project refuses to ship; being unable to answer is always preferable to answering
incorrectly.

Ask "how do I beat Anubis" and you get this, computed from your actual save, not
generated:

```
How to fight Anubis
--------------------
**Anubis** is Ground (field alpha).

**Caprity** | Grass | deals 2x | takes half
**Lifmunk** | Grass | deals 2x | takes half

2 shown | checked 2 of your Pals | equally matched on type - order is arbitrary
```

The element matchup, the multiplier, and which of *your* Pals qualify are all read out of
the game's own data tables and your local save file. The model — when one is involved at
all — only picks a phrasing and an order from a set a deterministic layer already
validated. Ask something the data can't back up, and the honest answer is a decline, not
a guess.

## Why voice in, Discord out

Mouse and keyboard are captured by the game client during play, so voice is the only free
input channel while actually playing. Answers post as a Discord card, readable through
the in-game overlay, a second monitor, or a phone. Text input works too, any time —
useful between sessions, or for anyone who'd rather not talk to a wake word.

## How it decides what to say

Every query lands in one of three tiers, by how much a model is trusted to contribute:

| Tier | Example | What the model may do |
|---|---|---|
| **1 — Fact** | *"where's the nearest coal"* | Route the question. Never touch the answer. |
| **2 — Computed advice** | *"how do I beat Anubis"*, *"where should I put a base for ore"* | Order and phrase a candidate set a validator already computed. May not add to it. |
| **3 — Open knowledge** | *"how does sanity work"* | Quote the game's own help text and patch notes, cited. No synthesis, no paraphrase that could drift from the source. |

A deterministic fast path answers what it can recognize with certainty — no model call,
no cost, no latency — and only ambiguous phrasing ever reaches a model. 13 query classes
are live today; the full list, and what's still open, is in
[`Docs/README.md`](Docs/README.md).

## Where the project actually is

This is documented obsessively, not left to memory. **[`STATUS.md`](STATUS.md)** is the
two-minute read on what's shipped, what's measured versus what only looks measured, and
what's still waiting on a decision. **[`Docs/04-roadmap.md`](Docs/04-roadmap.md)** is the
longer record of *how* each number was arrived at — including the ones that turned out to
be wrong, and why. Reading a stale impression of either is the most common way to be
confidently wrong about this project, so both get read at the start of every session, not
just skimmed once.

A few numbers that don't drift as fast as the rest: 879 tests, 13 production query
classes, one router call costs about half a cent.

## Running it

This isn't a five-minute clone-and-go — it expects a live Palworld install, a decrypted
save, and a Discord application, so treat the steps below as what's actually required
rather than a toy quickstart:

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

copy config.example.toml config.local.toml   # Discord token, channel id, save path
copy .env.example .env                       # API key(s) for the model router

.venv\Scripts\python -m palintel.ui          # console: status, config, start/stop
# or directly:
.venv\Scripts\python -m palintel.bot
```

Without any model credential configured, the bot still runs — it falls back to the
deterministic stub router and says so in the log, rather than failing silently.

`data/` is gitignored and generated from your own game install; see
[`Docs/03-data-ingestion.md`](Docs/03-data-ingestion.md) for the extraction and refresh
commands. Local GPU speech-to-text needs a CUDA-capable card; Discord voice *receive*
(for more than one speaker) needs a small sibling patch documented in
[`requirements.txt`](requirements.txt). Neither is required to run text-only.

## Documentation

[`Docs/README.md`](Docs/README.md) is the full index — architecture, data model,
ingestion, the multi-user design, and the Architecture Decision Records that explain why
several obvious-looking approaches were tried and discarded. Read the ADR log before
proposing anything that reintroduces one of them; it usually already has an answer for
why.

## License and data

The code in this repository is [MIT-licensed](LICENSE). The game data it reads —
Palworld's own tables, text, and artwork, extracted locally from your own install — is
**not included or redistributed**, stays under `data/` (gitignored), and belongs to its
respective owners. A handful of community-sourced values (ranch outputs, currently) are
labelled as such on every card they reach, separately from everything pulled directly
from the game's files.
