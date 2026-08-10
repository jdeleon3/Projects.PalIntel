# 03 — Data Ingestion Specification

## 1. Why this document exists

The original concept sketch gave data sourcing three bullet points. In practice it is the
**majority of the project's effort** and the source of most of its risk. Every accuracy
target in [00-overview.md](00-overview.md) §7 is bounded by what this pipeline produces.

The scope expansion to seven query classes roughly doubled the datasets required. Sequence
them against the roadmap rather than attempting them all at once.

## 2. Pipeline shape

```
[source] → [acquire] → [normalize] → [derive] → [validate] → [publish]
            raw cache   canonical     computed   invariants   data/v1.0.2/
                        schema        fields     + spot check
```

Each stage writes to disk. The raw cache is retained so normalization can be re-run without
re-acquiring — which matters when a source rate-limits or disappears.

Corpus ingestion (§4) adds a `chunk → embed` stage before publish.

## 3. Structured datasets

| Dataset | Serves | Phase | Difficulty |
|---|---|---|---|
| Resource nodes | Q1 | 1 | **High** — coordinate transform risk |
| Paldeck + spawns | Q2, lexicon | 2 | Low — well tabulated |
| Element matrix | Q5 | 3 | **Trivial** — 9×9, hand-entered |
| Bosses / towers | Q5 | 3 | Low — small, enumerable |
| Breeding ranks | Q3 | 3 | Conditional on A3 |
| Tech tree | Q6 | 4 | Medium — prerequisite graph |
| Base sites | Q4 | 4 | Low volume, hand-curated |
| Card artwork | Q1, Q2 presentation | spike | Low — see §3.7 |
| Pal drop items | Q1 answer line | spike | Low — see §3.8 |

### 3.1 Resource nodes (Q1 — v1 critical path)

**Needs:** coordinates, resource type, cluster size, surrounding threat level, region.

**Source: extract from the game's own `.pak` files.** Community interactive maps are
cross-validation, not the origin. See [ADR-0014](adr/0014-game-files-as-source.md).

Phase 0.5 survey established that the leading community maps derive their marker
coordinates from PAK extraction themselves, then verify against in-game tile coordinates.
Extracting directly puts us at the same source rather than one hop downstream of it.

Path: `tools/extract/PakExtract` (CUE4Parse, .NET 10) against the local game install,
using the community Palworld mapping file. **No AES key is needed** — the pak carries a
zero encryption GUID and `bEncryptedIndex=0`.

**Actor positions are not uniformly in world space.** Nodes scattered by a designer
placement volume (`BP_BoxPlacementTool_*`) store `RelativeLocation` relative to that
volume; taken literally they collapse onto world origin, which maps to a plausible-looking
but empty spot. The extractor walks each actor's `Owner` chain and composes parent
transforms to recover world positions. Anything whose owner lies outside the loaded cell
is excluded rather than guessed.

Hazards:
- **Coordinate space ambiguity** — see [02-data-model.md](02-data-model.md) §2. Establish
  and validate the transform before ingesting at volume. This is assumption **A4** and the
  only hard gate on v1.
- **Cluster granularity** — sources disagree on whether a node is one deposit or a cluster.
  Normalize to cluster with an explicit `node_count`.
- **Scope disagreement between sources** — one surveyed source reports 553 coal nodes;
  another reports 1,021 across 119 maps, the latter likely including dungeon instances.
  Overworld and instanced-dungeon nodes must be distinguished explicitly, and a node count
  that matches neither source is a validation failure, not a rounding difference.

  **This hazard was real and went unenforced until 2026-08-10.** The distinction is in the
  cell name: World Partition streams the overworld as `MainGrid_L0_X<x>_Y<y>_DL<hash>`,
  where the grid position is where the cell sits in the world, while cave and dungeon
  contents live in `L15_X0_Y0` — a single cell at the grid origin, because they are
  authored in their own local space rather than placed on the map. Run through the
  overworld transform those coordinates are meaningless. **6,718 of 40,968 deposits
  (16.4%)** were published as overworld positions, including **672 of 998 coal**, and the
  dataset's own `scope` field claimed "overworld only" the whole time. Now filtered by
  `is_overworld` in `build_resource_nodes.py`, with a regression guard in
  `tests/test_node_scope.py`. Pal spawn areas were unaffected — exactly 1 of 13,895.
- **`min_player_level` does not exist upstream.** It is derived (§5).

#### 3.1.1 Deriving the coordinate transform

The expected form is a linear map from UE world coordinates to in-game map coordinates:

```
map_x = (world_y - offset_y) / scale
map_y = (world_x - offset_x) / scale
```

Axis swap and sign conventions are unverified — UE's world axes do not necessarily align
with the in-game map's, and this is exactly where a silent systematic error would enter.

Fit against ≥ 3 known landmarks (fast travel points are ideal: fixed, unambiguous, and
readable in-game), then **validate against ≥ 20 independent nodes by standing on them
in-game and reading the map coordinate**. Fit and validation sets must be disjoint — a
transform that reproduces its own fit points proves nothing.

Record the resulting transform as a versioned `transform_id` on every `Coord`.

### 3.2 Paldeck, spawns, and base stats (Q2, Q5, lexicon)

**Needs:** name, deck number, elements, work suitability, base stats, partner skill text,
combination rank.

Generally the cleanest dataset — well tabulated across sources, so cross-validation between
two independent sources is cheap and worth doing.

This dataset **generates the lexicon**, making it a dependency of the STT layer, not just
Q2. Ingest early despite Q2 being post-v1. It also supplies `base_stats` for Q5 scoring.

### 3.3 Element matrix and bosses (Q5)

The element matrix is nine elements with exact multipliers — **enter it by hand and unit
test it.** It is small enough that scraping introduces more risk than it removes.

Bosses need elements, level, location, and `tower_order`. Tower bosses are a short
enumerable list; field alphas are larger but still bounded. `tower_order` must be correct —
conversation memory relies on it to resolve *"the next tower"*.

### 3.4 Breeding data (Q3)

Strategy depends entirely on assumption **A3**
([02-data-model.md](02-data-model.md) §5):

- **If A3 holds:** one integer per Pal plus an exception table. Hundreds of rows.
- **If A3 fails:** scrape explicit parent pairs. Tens of thousands of rows, materially more
  per-patch maintenance.

**Validation gate:** derive the full combination table from ranks and check against ≥ 100
known combinations from an independent source. Require **100% agreement** outside the
declared exception table. Partial agreement is failure, not a tunable — a model that is
right 95% of the time produces confidently wrong chains 1 in 20 times, discovered only
after the player has invested eggs and hours.

### 3.5 Technology tree (Q6)

**Needs:** tech id, name, required level, point costs, prerequisites, category, description.

The prerequisite graph is the part to get right; a missing edge produces recommendations
the player cannot actually take. Validate that the graph is acyclic and that every
prerequisite resolves.

Pairs with assumption **A6** — that the save exposes unlocked tech. If A6 fails, Q6 asks
the player what they have rather than reading it, degrading the experience but not the
correctness.

### 3.6 Base sites (Q4)

Unlike the others this is **opinion, not fact** — sourced from guides and community
consensus, and reasonable players disagree. Consequences:

- Attribute every site in `source_attribution`
- Keep the corpus small and hand-curated — twenty well-described sites beat two hundred
  scraped ones
- Cards must be marked as advice

### 3.7 Card artwork (presentation — spike)

**Needs:** a world-map basemap per region with its world-space bounds, and one icon per
Pal. See [ADR-0017](adr/0017-card-artwork-from-game-assets.md).

Sources are all in the pak, via `PakExtract.exe textures`:

| Asset | Path | Shape |
|---|---|---|
| Map bounds | `DT_WorldMapUIData` | 2 regions with `landScapeRealPosition{Min,Max}` and a priority |
| Basemaps | `Texture/UI/Map/T_WorldMap`, `T_TreeMap` | 8192², PF_DXT1 |
| Pal icons | `Texture/PalIcon/Normal/T_<InternalId>_icon_normal` | 424 files, 128², PF_DXT5 |
| Item icons | `Others/InventoryItemIcon/Texture/T_itemicon_<Category>_<ItemId>` | 796 files, 256² |

Pal icons join the lexicon on `internal_ids` — no new key. Coverage is **285 of 286
Paldeck entries**; the gap is Rayhound Cryst, which has no icon in the pak.

Item icons join a resource to its drop via `_resources.item_ids()`, which re-walks the
same spawner → drop chain as `derive()` so the two cannot be updated apart. The item id
is *not* the canonical id — `ore` is `CopperOre`, `paldium_fragment` is `Pal_crystal_S` —
and the filename keeps the game's category prefix because item ids contain underscores of
their own. **17 of 18 resources**; `cavern_mushroom` (`CaveMushroom`) has no icon, and
the several other mushroom icons are near enough to be a tempting guess and are not it.

**What an item icon is not.** It shows the material in your inventory, not the node in the
world — and the world appearance is what a player actually needs to recognise a deposit.
The game carries no 2D art for it: map objects have no icon field, only meshes. Rendering
those is a materially larger job (mesh, material, lighting) and is not in scope.

Hazards, both of which produce an authoritative-looking wrong picture rather than a
visibly broken one:

- **There is more than one map.** 1,269 extracted placements fall inside the Tree
  rectangle and outside MainMap's. A coordinate is matched to a region by bounds and
  priority, and one matching none gets no picture.
- **Bounds do not imply orientation.** Which world axis drives the image column, and
  whether either runs backwards, is a separate fact — and it is again an axis swap, as in
  §3.1.1. It is *measured*, not assumed: three independent classifiers score all eight
  layouts using every extracted placement as known terrain, and the build fails closed
  unless they agree unanimously.

Published to `data/<version>/assets/` as 512 px tiles plus a 1024² whole-region overview
(two zoom levels, so a widely-spread answer stays cheap), and gitignored per §7 — this is
game *art*, the clearest case of all for not redistributing.

### 3.8 Pal drop items (Q1 — "also drops from")

**Needs:** which Pals yield a locatable resource when defeated or captured.

Source is `DT_PalDropItem_Common` — 1,044 rows keyed by `CharacterID`, ten fixed item
slots each with a rate and a count range. Extracted verbatim by `PakExtract.exe paldrops`
and inverted in `build_pal_drops.py`, where the lexicon lives. **11 of 18 resources have
a dropper**; stone, wood and the World Tree materials have none.

It earns a place on a *locations* card because it is most useful when the locations are
not: the nearest coal may sit in a level 40 zone, and farming a Blazamut is a route
available at a level where walking there is not. For that reason it renders on the
no-results card too, where it turns a dead end into an answer.

Three ingest judgements, all published on the dataset under `rules` because each changes
what the card's line claims:

- **Rate-0 rows are excluded.** The table carries real rows with `Rate: 0` — Smokie Cryst
  for coal, Neptilius for quartz, Tetroise for sulfur. Naming a Pal that never yields the
  item is a fabricated value in a slot the player would act on. 48 rows dropped.
- **Boss variants are credited to the base species, and that is an inference.**
  `BOSS_RockBeast` is read as the alpha of `RockBeast` — derived from the naming, not
  stated by the data. `alpha_only` marks any dropper seen *only* on a variant row, so a
  card can say "alpha" rather than implying an ordinary encounter. Currently **zero**
  published droppers are alpha-only, so no claim depends on the inference; a test asserts
  that, and fails loudly if a patch changes it.
- **Quest and NPC actors are excluded.** `_Quest`, `_Avatar` and human enemies share a
  base name with real Pals, so stripping the suffix would credit a Pal with a drop only
  its scenario version has.

One upstream quirk worth recording: **the drop table's casing disagrees with the name
table's.** It carries `Gorilla_ground`, `KingBahamut_dragon`, `SkyDragon_grass` and
`Drillgame` against the lexicon's `Gorilla_Ground`, `KingBahamut_Dragon`,
`SkyDragon_Grass` and `DrillGame`. Matching exactly silently lost five real droppers, so
the join is case-insensitive.

### 3.9 Ranch production (community-sourced — the one exception)

**Needs:** which Pals can be assigned to a Ranch, and what each produces.

**Source: [palworld.wiki.gg/wiki/Ranch](https://palworld.wiki.gg/wiki/Ranch), not the game
files.** This is the project's only dataset whose facts come from a community site, and it
is a scoped exception to [ADR-0014](adr/0014-game-files-as-source.md) — see the amendment
there. The ranch spike enumerated all 284 data tables and found nothing mapping a Pal to
its output; the mapping is in blueprint bytecode.

The **roster** is still extracted (`PakExtract.exe ranch` → one
`BP_Action_SpawnItem_<CharacterID>` asset per Pal) and is what validates the wiki:

| | |
|---|---|
| Wiki rows parsed | 29 |
| Published | 29, of which **28 corroborated** by the pak roster |
| Flagged `roster_verified: false` | 1 — Mau Cryst, no `Bastet_Ice` action asset exists |

The check is **asymmetric, and that was measured rather than assumed**. The roster also
contains Snock, Teafant, Direhowl and Tarantriss, so the asset means "has an
item-spawning action", which is broader than "is ranchable". A wiki row *off* the roster
is a real flag; a roster entry with no wiki row is weak evidence and is reported as such.

One naming inconsistency is the game's own and is aliased explicitly rather than matched
fuzzily: Woolipop is `SweetsSheep` in the parameter and name tables but `SweetSheep` in
its action asset.

Per §7, the page is cached to `data/raw/ranch_wiki.md` so normalisation can be re-run
without re-fetching, `provenance` and the source URL are fields on the published dataset,
and nothing is redistributed. **Finding an authoritative in-game source is on the backlog.**

## 4. Knowledge corpus (Q7 / Tier 3)

A different pipeline shape: prose, chunked and embedded rather than normalized into a
schema.

**Sources.** Community wikis and written guides. Selection criteria, in priority order:

1. **Licence permits reuse** — checked *before* acquisition, not after
2. Content is structured with headings (drives semantic chunking)
3. Maintained against current patches
4. Covers systems and mechanics, not just data tables — tables are better served by the
   structured datasets

**Chunking.** Split on headings and sections, not fixed token windows. Wiki content is
already sectioned; respecting that keeps chunks self-contained and makes citations
meaningful. Target roughly 200–500 tokens, preferring a natural boundary over an exact size.

**Entity tagging.** Tag each chunk with the canonical entities it mentions, using the same
lexicon that drives STT correction and routing. This powers hybrid retrieval
([02-data-model.md](02-data-model.md) §6) and is the difference between *"what does Artisan
do"* returning the Artisan section versus something merely topically adjacent.

**Embedding.** Any competent text embedding model. Run once at ingest; vectors ship in
`corpus.sqlite`. There is no runtime embedding of the corpus — only of the incoming query.

**Coverage tracking.** Maintain a checklist of game systems the corpus should cover
(traits, breeding mechanics, base mechanics, status effects, technology, raids, …). Gaps
are visible and fillable. This is what makes assumption **A7** manageable: coverage grows
deliberately rather than being discovered as failures.

**Exclusions.** Do not ingest prose that duplicates structured data. If a wiki page lists
ore coordinates, that belongs in `resource_nodes.sqlite` and answering from prose would
bypass every guarantee Tier 1 provides.

## 5. Derived fields

Derived fields are **opinions expressed as data**. Each needs a documented rule applied
uniformly, because results are gated on them.

### `ResourceNode.min_player_level`

Gates every Q1 result — it determines whether the system sends the player somewhere they
will die.

Proposed rule:

```
min_player_level = ceil(max_local_wild_pal_level * 0.8)
  +5 if node sits inside raid-triggering territory
  +5 if danger == HIGH
```

**Implemented in Phase 2 as `local-wild-pal-level-v1`**, with two departures from the
proposal, both recorded in `difficulty_inputs` on the published dataset:

- **`local_wild_level` is a weighted 90th percentile, not the maximum.** The literal
  maximum does not survive contact with the data. In the level 1–7 starting area a
  Mammorest spawns on a 1% roll at level 33–35; taking the max makes the beginner zone a
  level-35 region, and it rated 65% of every node on the map "high" danger with a median
  gating level of 44. Each nearby spawn area is weighted by its expected encounter rate
  (spawn points × the share of rolls producing that species), and the level is read at
  p90 — the hardest *common* encounter, which is what sets the danger of a place. Checked
  against four zones of known difficulty: starter 35→7, desert 53→42, volcano 56→56,
  Feybreak 72→68.
- **The raid-territory term is not applied.** Raid territory is not in any extracted
  table, and a proxy would make the rule untraceable to its inputs.

"Local" is 50 map units (~230 m) — spawn areas cluster at 25, so this is the Pals you meet
walking in. Field alphas are excluded: a level 55 boss beside a starter-zone node would
gate it at 44 and hide a place low-level players actually farm.

**Still uncalibrated.** This section asks for ~20 nodes of known difficulty read in-game
and that has not been done, so the rule is checked for self-consistency and against four
reference zones rather than validated. Recorded as a known gap on the dataset.

### `ResourceNode.danger`

From local Pal levels. Three buckets — resist adding precision the underlying data cannot
support: `low` ≤ 20, `moderate` 21–40, `high` > 40, at the boundaries where the game's own
progression gates sit. Proximity to hostile camps is *not* an input; camp placements have
not been extracted.

### `ResourceNode.resource`

**Derived, not hand-mapped**, since Phase 2. The chain is entirely in the game data:

```
spawner CDO -> MapObjectId -> map object master table -> DropItems[].StaticItemId
```

A node with several drops is named by its largest. What counts as a locatable resource is
the game's own item category — `MaterialOre`, `MaterialStone`, `MaterialWood`,
`FoodVegetable` — which admits the mined and gathered materials and excludes the stat
lotuses, Dog Coins and Kinship Peaches.

This replaced a six-entry hand-written map, and **reading blueprint names had got two of
the six wrong**: `SkyIslandOre` yields Soralite and `WorldTreeOre` yields Paloxite,
neither of which is Ore, and both shipped as `ore` through the whole of Phase 1 — 306
clusters telling a player they had found ore. `RockIron` would have been guessed as iron;
it yields Pure Quartz.

### `BaseSite.flatness_score`

Hand-curated, not computed. Terrain data is not reliably available, and a fabricated score
is worse than an honest human judgment.

## 6. Validation

Ingestion **fails closed**. Data that does not validate is not published — a silently wrong
dataset produces confidently wrong cards, the failure mode this project is least willing to
accept.

**Structural**
- Schema conformance and type checking on every row
- No duplicate `node_id` / `pal_id` / `tech_id` / `chunk_id`
- Coordinates within known map bounds
- Every referenced `pal_id` exists in `pals.json`
- Tech prerequisite graph is acyclic; every prerequisite resolves

**Semantic**
- Every Pal has ≥ 1 spawn zone or an explicit `breeding_only` flag
- Every `ResourceType` has ≥ 1 node
- Combination ranks unique and contiguous
- Element matrix is complete (9×9) and unit tested against known matchups
- Every boss has ≥ 1 element and a level
- Derived `min_player_level` within 1–60

**Corpus-specific**
- Every chunk has a resolvable `source_url` and non-empty `source_title`
- Embedding dimensionality uniform across the corpus
- No chunk exceeds the model's context contribution budget
- Coverage checklist reports which systems have zero chunks

**Cross-source**
- Paldeck stats agree between two independent sources; disagreements surfaced for manual
  adjudication, never auto-resolved
- ≥ 20 node coordinates verified by hand against the in-game map

**Regression**
- On patch re-ingest, diff against the previous version. Changes above a threshold
  (e.g. > 10% of rows) require manual sign-off — this catches upstream format changes that
  would otherwise silently corrupt the dataset.

## 7. Legal and ethical constraints

- **Check licences before acquisition**, particularly for the prose corpus, where
  substantial text is reproduced rather than facts extracted.
- **Respect `robots.txt` and rate limits.** Ingestion is one-off per patch. Throttle
  deliberately; there is no time pressure.
- **Attribute sources** in the published data, in Tier 3 cards, and in the repo README.
- **Do not redistribute** scraped datasets as a standalone product. Data lives here to make
  a personal tool work.
- **Prefer official APIs or bulk exports** where offered.
- If a source's terms prohibit scraping, **do not scrape it** — find another source or
  curate by hand.

## 8. Refresh workflow

```bash
# Card artwork, optional and separate: it is game art rather than extracted facts, and
# nothing in the answer path depends on it.
dotnet run --project tools/extract/PakExtract -- textures
python tools/ingest/build_assets.py --version 1.0.2

# "Also drops from" on resource cards. Also optional - Q1 answers without it.
dotnet run --project tools/extract/PakExtract -- paldrops
python tools/ingest/build_pal_drops.py --version 1.0.2

# Ranch production. The roster comes from the pak; the items come from the wiki page
# cached at data/raw/ranch_wiki.md (section 3.9), so refresh that file on a patch.
dotnet run --project tools/extract/PakExtract -- ranch
python tools/ingest/build_ranch.py --version 1.0.2

palintel-ingest   --version 1.0.2 --source-config sources.yaml
palintel-corpus   --version 1.0.2 --embed          # chunk + embed prose
palintel-validate --version 1.0.2 --compare-to 1.0.1
palintel-publish  --version 1.0.2                  # writes data/, updates `current`
```

Ingestion tooling lives in the repo but is **not** part of the runtime process. The bot
loads published data and has no scraping or embedding-of-corpus capability at runtime.
