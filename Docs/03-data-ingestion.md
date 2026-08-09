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

Proposed rule, calibrated in Phase 0:

```
min_player_level = ceil(max_local_wild_pal_level * 0.8)
  +5 if node sits inside raid-triggering territory
  +5 if danger == HIGH
```

Calibrate against ~20 nodes of known difficulty. Record the final rule **and its version**
in the published data so an answer is traceable to the rule that produced it.

### `ResourceNode.danger`

From local Pal levels and proximity to hostile camps. Three buckets — resist adding
precision the underlying data cannot support.

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
palintel-ingest   --version 1.0.2 --source-config sources.yaml
palintel-corpus   --version 1.0.2 --embed          # chunk + embed prose
palintel-validate --version 1.0.2 --compare-to 1.0.1
palintel-publish  --version 1.0.2                  # writes data/, updates `current`
```

Ingestion tooling lives in the repo but is **not** part of the runtime process. The bot
loads published data and has no scraping or embedding-of-corpus capability at runtime.
