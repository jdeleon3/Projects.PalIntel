# Coordinate transform verification — what to fill in

> **Note for anyone cloning this repo.** The game-derived files described below
> (`coord_transform_verification.csv`, `all_boss_landmarks.csv`, `1.0.2/`) are **not
> committed**. Extraction from a game you own supports a personal tool; republishing the
> result as a dataset is a different act — see
> [ADR-0014](../Docs/adr/0014-game-files-as-source.md) and
> [03-data-ingestion.md](../Docs/03-data-ingestion.md) §7. Regenerate them by running
> `tools/ingest/` against your own Palworld install.
>
> `coord_transform.json` **is** committed: it is a derived formula, not game content.

Extracted from `Pal-Windows.pak` (game v1.0.2) via the headless CUE4Parse pipeline.
These files exist to resolve **assumption A4**, the only remaining hard gate on v1
([../Docs/04-roadmap.md](../Docs/04-roadmap.md) Phase 0.5).

## The problem

The game stores positions in **UE world coordinates** (centimetres, roughly
±1,000,000). The in-game map shows something else entirely — the small numbers you
read off the map, like `(-160, -84)`. Every Q1 answer displays the latter, so we need
the transform between them, and we need it verified rather than assumed.

## Current state

The transform is **derived and provisional** — see `coord_transform.json`. It was fitted
on the 4 `FIT` rows, which are now filled in.

`predicted_map_x` / `predicted_map_y` hold what the transform *says* each location should
read. That makes validation quick: go there, glance at the map, confirm it roughly matches.

## What's left to do

Fill in `map_x` / `map_y` on the 20 `VALIDATE` rows as you happen to pass them during
normal play. No special trips needed. `delta_x` / `delta_y` are computed afterwards.

**Record what the map actually shows.** Don't round toward the predicted value — a large
disagreement is exactly the finding this step exists to surface, and a prediction that
quietly becomes its own confirmation is worse than no validation at all.

These 20 points are deliberately disjoint from the 4 used to fit. Fit residuals only
measure how well a model reproduces its own inputs; only independent points show whether
it generalises.

### Likely unreachable

Points **8** and **20** are in the World Tree region, and point **16** is on an oil rig.
Substitute any row from `all_boss_landmarks.csv` — spread across the map matters far more
than which specific landmarks are used.

## If a location is inconvenient

`all_boss_landmarks.csv` lists all 159 extracted field-boss spawn points with their
world coordinates. Swap any row in the verification file for one that's easier to
reach — spread matters far more than which specific points you use. Four landmarks
clustered in one region produce a poorly conditioned fit; four spread across the map
produce a good one.

## Column reference

| Column | Meaning |
|---|---|
| `point_id` | Row number, for reference |
| `role` | `FIT` (used to derive the transform) or `VALIDATE` (independent check) |
| `kind` | Landmark type — currently all `boss` |
| `source_id` | The game's internal spawner ID |
| `name` | Internal character ID. `None` means unnamed in the data table |
| `world_x/y/z` | UE world coordinates, from the pak. **Do not edit.** |
| `predicted_map_x/y` | What the transform predicts. **Do not edit.** |
| `map_x/map_y` | **← you fill these in** — what the in-game map actually displays |
| `delta_x/delta_y` | Computed: predicted minus observed. Leave blank. |
| `notes` | Boss level, useful for judging whether it's safe to approach |

Predictions and observations are kept in **separate** columns on purpose. Writing
predictions into the observation columns would erase the only quantity validation
actually measures.

## Why field bosses

They're fixed, unambiguous, marked on the map once discovered, and spread across every
region. Fast travel points would have been easier to reach, but `DT_RespawnPointInfo`
turned out to hold spawn-region metadata (`ResourcesAbundant`, `PalAbundant`) with no
coordinates at all.

Reading your position from the save file would have been easier still, but the player
position lives in `Level.sav`'s character blobs — behind the same stale `RawData`
decoder that Phase 0.3 found broken on 1.0.2. So it isn't currently available.

## Provenance

| File | Source |
|---|---|
| `coord_transform_verification.csv` | `DT_BossSpawnerLoactionData`, farthest-point sampled for spatial spread |
| `all_boss_landmarks.csv` | `DT_BossSpawnerLoactionData`, all 159 rows |

World coordinate extent observed: X `-1,033,348 … 601,097`, Y `-733,420 … 575,683`.
