# PalIntel

A voice and text assistant that answers Palworld questions while you play — standalone,
with its own local UI, or through Discord if that's where you already are. One rule
shapes everything else: **coordinates, stats, and breeding pairs never originate from a
language model.** A card that's confidently wrong is the one failure this project refuses
to ship; being unable to answer is always preferable to answering incorrectly.

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

## Why voice in, and two places it can go out

Mouse and keyboard are captured by the game client during play, so voice is the only free
input channel while actually playing. Text input works too, any time — useful between
sessions, or for anyone who'd rather not talk to a wake word.

Where the answer goes is one config choice, `output.medium`, and the two paths never
touch each other's code:

- **`discord`** — the original, played-tested path. Answers post as a Discord card,
  readable through the in-game overlay, a second monitor, or a phone. Real play
  sessions, measured latency, real spend data — most of `STATUS.md` is this path.
- **`local`** — no Discord account needed at all. A small console (`palintel.ui`) opens
  a Chat tab in the browser and renders the same cards, the same tier colours, the same
  artwork. **This path is new** — built and automated-tested, not yet played the way
  Discord mode has been, so treat it as "works" rather than "proven" until it's logged
  some real sessions of its own.

Either way, the answer is computed the same way by the same pipeline; the medium only
decides where the card is drawn.

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

A few numbers that don't drift as fast as the rest: 941 tests, 13 production query
classes, one router call costs about half a cent.

## Running it

This isn't a five-minute clone-and-go — it expects a live Palworld install and a
decrypted save; a Discord application is only needed if you actually want that output
medium, so treat the steps below as what's actually required rather than a toy
quickstart:

```
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

copy config.example.toml config.local.toml   # save path, output medium, and (if
                                              # output.medium = "discord") its token
copy .env.example .env                       # API key(s) for the model router

.venv\Scripts\python -m palintel.ui          # console: status, config, start/stop
# or directly:
.venv\Scripts\python -m palintel.bot
```

`config.local.toml`'s `[output]` section picks the medium — `medium = "discord"` (the
default, needs `discord.token` / `discord.channel_id`) or `medium = "local"` (needs
neither; open the console and use its Chat tab once the bot is running).

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
