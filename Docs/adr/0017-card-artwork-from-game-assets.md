# ADR-0017 — Card artwork from game assets, drawn off the graded path

**Status:** Provisional — spike, behind `[cards] maps/icons`, default off
**Amends:** [0006](0006-templated-cards.md)

## Context

Two enhancement requests: show a map with the answer's locations marked, and show each
Pal's picture on Pal queries.

Both look like presentation, and [ADR-0006](0006-templated-cards.md) has already been
through one round of this — it removed LLM-generated cards because a formatting step was
adding hallucination risk, latency and cost to values that were already known and typed.
That reasoning applies to *generated* content. A map crop and an icon are neither
generated nor guessed: both are game assets selected by a typed result, and the map
plots the same coordinates the card already prints. Nothing new enters the answer.

What *is* new is a way to be wrong that text does not have. A coordinate a player cannot
parse is obviously useless. A marker on the wrong island looks exactly like a marker on
the right one, and it is read at a glance mid-play, which is the reading mode least
likely to catch it. That is the failure this ADR is organised against, not fidelity.

Three findings from the spike shaped the decision.

**There is more than one map.** `DT_WorldMapUIData` publishes two regions with explicit
world-space rectangles — MainMap and Tree — and 1,269 of the extracted placements fall
inside the Tree rectangle and outside MainMap's. Plotting everything on the main island
would put those markers in open sea.

**The pixel orientation is not implied by the bounds.** The bounds say which world
rectangle a texture covers, not which way round it is drawn. This is the same trap that
[spike 0.5](../04-roadmap.md) hit deriving the world → map transform, where the axes
turned out to be swapped; here the answer is again an axis swap, plus an inverted row.

**Illustrating inside the answer path costs the answer.** Rendering during `handle()`
moved p95 from 76 ms to 508 ms, because a Pal with spawn areas 1,000 map units apart
needs a crop spanning 3,570 source pixels.

## Decision

**Artwork is attached to cards from published game assets, and is never load-bearing.**

1. **Assets are extracted, not fetched.** The two basemaps and 424 Pal icons come from
   the pak via the existing `PakExtract` path, consistent with
   [ADR-0014](0014-game-files-as-source.md), and are gitignored like every other
   game-derived artifact ([03-data-ingestion.md](../03-data-ingestion.md) §7). Icons join
   the lexicon on `internal_ids`, which already exists.

2. **World → pixel comes from the game, orientation from measurement.** The rectangles
   are the game's own. The orientation is chosen by three independent classifiers voting
   over the eight candidate layouts, using every extracted placement as known terrain.
   Unanimity is the gate, not any single margin — each classifier is weak on one of the
   two maps, and picking whichever looked decisive on the map in front of me would be
   fitting the measurement to its example. The ingest **fails closed** if they ever
   disagree.

3. **A coordinate belonging to no region gets no picture, and points straddling two
   regions get none either.** Not a clamp, not a best effort, not the region holding the
   most points — a picture that shows two of three answers without saying so disagrees
   with the text above it about how many answers there are.

4. **Rendering happens after the answer is posted.** `Outcome` carries a deferred
   `illustrate`; the bot sends the text embed, then renders and edits the attachment in
   on a second round trip. The graded promise is "here are the coordinates" and a picture
   is not part of it.

5. **Two zoom levels, so a wide answer is bounded rather than refused.** Some Pals really
   are found in two corners of the map. Above a 4× crop the tiles carry detail the output
   cannot show, so it reads a whole-region overview instead.

6. **Off by default.** Whether a picture helps or clutters is a judgement about reading
   cards mid-combat, which only real sessions settle.

## Measurements

| | text only | artwork on |
|---|---|---|
| `handle()` p50 / p95 | 51.2 / 76.4 ms | 52.8 / 77.9 ms |
| deferred draw p50 / p95 / max | — | 7.8 / 25.5 / 25.9 ms |
| payload per card | — | p50 65 KB, max 87 KB |

Orientation vote, fraction of known-terrain placements landing on terrain:

| classifier | MainMap | Tree |
|---|---|---|
| land colour | 77.8% vs 46.9% | 58.0% vs 43.5% |
| not background | 95.9% vs 93.6% | 93.6% vs 79.9% |
| local detail | 69.8% vs 51.2% | 67.6% vs 57.9% |

Icon coverage: 296 of 313 lexicon entries; **285 of 286 Paldeck entries**. The single
Paldeck gap is Rayhound Cryst (`ThunderDog_Ice`), which has no icon asset in the pak at
all. The remaining misses are tower-boss human+Pal pairs, Terraria collab entities and
PIDF Rider — none of them Paldeck members. **Resource cards carry no thumbnail.** The
item's inventory icon was shipped there, read in play, and withdrawn: it joined cleanly at
17 of 18 resources and still showed the wrong thing, since recognising a deposit is a
question about the rock in the world rather than the item in your pack.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Full-region map with markers, always** | At 2.59 px per map unit a node cluster is sub-pixel on a downscaled 8192² island. The picture would be decorative, which is the [ADR-0006](0006-templated-cards.md) failure in a new medium. Kept as the *fallback* level for wide spreads, where it is the honest framing. |
| **Pre-render every cluster's crop at ingest** | Near-zero query cost, but 2,668 clusters, stale on any data change, and no way to draw the player's own position — which is the marker that makes "nearest" mean anything. |
| **Send the text and the attachment in one message** | Simpler, no reflow. It charges every illustrated query the upload's latency, against a bar that is already unmet ([00-overview.md](../00-overview.md) §7). |
| **Assume the obvious pixel orientation** | It is wrong. Column comes from world Y and the row axis is inverted; assuming the naive pairing puts every marker in the wrong place, confidently. |
| **Clamp out-of-region coordinates onto the nearest map** | Produces the exact output this project exists to prevent: authoritative-looking and wrong. |
| **Higher-resolution Pal art** | The icons are 128×128, which suits a thumbnail and would be soft in the full image slot. Larger portrait art may exist elsewhere in the pak; not surveyed, and not needed for a thumbnail. |
| **A picture of the node as it appears in the world** | This is what a player actually needs — "I looked for quartz for two days because I thought it looked different" is a recognition failure, not a navigation one. No 2D asset exists: map objects carry no icon field, only meshes, so it needs an offline mesh render with materials and lighting. Worth revisiting as its own piece of work; the item icon is a partial substitute and is described as one. |

## Consequences

**Positive**
- The graded latency path is unchanged, measured.
- Both new failure modes — wrong region, partial coverage — fail to *no picture*, which
  is the existing degradation principle ([01-architecture.md](../01-architecture.md) §8).
- The map bounds are an independent corroboration of the fitted `coord_transform.json`:
  two unrelated derivations of the same world geometry that agree.

**Negative**
- A second Discord round trip per illustrated answer, visible as a reflow.
- ~20 MB of local assets and a two-step build per patch.
- Pillow becomes a dependency, optional at runtime.
- Every new card type needing artwork needs an `illustrate_*` method — the same per-type
  hand-written cost [ADR-0006](0006-templated-cards.md) accepted for templates.

**Unresolved**
- **The Discord edit round trip is unmeasured.** Everything above is local. Whether the
  reflow reads as helpful or as clutter is the question the flag exists to answer.
- `coord_transform.json` was fitted on MainMap landmarks only. Tree-region coordinates go
  through it unvalidated, so a Tree marker's *position on our own crop* is consistent with
  the coordinates the card prints, but neither has been checked against the in-game Tree
  map. Pre-existing, not introduced here, and it now has a visible surface.
- Markers at near-identical coordinates overlap. The numbers are also in the card text,
  so nothing is unreadable, but two overlapping discs read as one.
