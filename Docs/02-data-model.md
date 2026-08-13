# 02 — Data Model and Storage

## 1. Two stores, different reasons

| Store | Contents | Access | Serves |
|---|---|---|---|
| **Knowledge Base** | Structured game data — nodes, Pals, spawns, elements, tech tree | Typed queries over in-memory structures | Tier 1, Tier 2 |
| **Knowledge Corpus** | Chunked prose from wiki and guides | Local vector index, similarity search | Tier 3 |

The split is the data model expression of
[ADR-0010](adr/0010-three-tier-answer-model.md). Structured data gets structured queries;
prose gets retrieval. Applying retrieval to the structured half was the original design's
central error ([ADR-0001](adr/0001-drop-vector-search-premise.md)); refusing it for the
prose half would be the mirror-image error.

Both stores are **local files**, versioned by game patch. No external service.

```
data/
  v1.0.2/                        # keyed by Palworld patch version
    pals.json                    # specs, work suitability, elements, combination rank
    breeding_exceptions.json     # special-case parent pairs
    elements.json                # elemental effectiveness matrix
    bosses.json                  # tower & field bosses: elements, level, location
    tech_tree.json               # technology nodes, prerequisites, costs
    resource_nodes.sqlite        # coordinates, resource, level, danger
    spawn_zones.sqlite           # pal → zone → time of day
    base_sites.json              # curated candidate sites + prose rationale
    lexicon.json                 # canonical entity names + phonetic variants
    corpus.sqlite                # chunked prose + embeddings + source attribution
  current -> v1.0.2/
```

JSON for hand-auditable reference data; SQLite where a query engine or vector index earns
its keep. All committed to the repo.

## 2. Coordinate system

Palworld's in-game map coordinates and internal world coordinates are **not** the same
space, and community sources vary in which they publish.

**Rule: store both.** Persist raw source coordinates alongside a normalized in-game map
coordinate, recording which transform was applied.

```python
@dataclass(frozen=True)
class Coord:
    map_x: float          # in-game map coordinate — what the player reads
    map_y: float
    world_x: float | None = None
    world_y: float | None = None
    transform_id: str | None = None   # provenance of the conversion
```

Cards always display `map_x`/`map_y`. Deriving and validating this transform is assumption
**A4** and a Phase 0 task — a systematically wrong transform makes every Q1 answer
confidently incorrect, the worst failure mode available here.

## 3. Tier 1 entities

### 3.1 ResourceNode

```python
class ResourceType(StrEnum):
    ORE = "ore"; COAL = "coal"; SULFUR = "sulfur"; QUARTZ = "quartz"
    # CRUDE_OIL removed, then RESTORED 2026-08-12. The removal reasoned from "extraction
    # found no BP_PalMapObjectSpawner class for it" to "crude oil is not a placed node",
    # which is a claim about the world drawn from the shape of a filter. There are 185
    # BP_LevelObject_OilField_C actors; the blueprint states ProvidableStaticItemId:
    # CrudeOil; and the game's item text says "Obtained by installing a Crude Oil
    # Extractor in an oil field." Q1 answers "where is oil" today - see the note below.

class DangerRating(StrEnum):
    LOW = "low"; MODERATE = "moderate"; HIGH = "high"

@dataclass(frozen=True)
class ResourceNode:
    node_id: str
    resource: ResourceType
    coord: Coord
    node_count: int              # deposits clustered here
    min_player_level: int        # DERIVED — see 03-data-ingestion §4
    danger: DangerRating
    region: str
    notes: str | None = None
```

`min_player_level` is a **derived, opinionated** field, not a game constant. Its rule must
be documented and applied uniformly — it gates every Q1 result.

### 3.2 Pal

```python
@dataclass(frozen=True)
class Pal:
    pal_id: str
    name: str                             # canonical, matches lexicon
    paldeck_no: int
    elements: list[Element]
    work_suitability: dict[WorkType, int]
    base_stats: PalStats                  # hp, attack, defence — feeds Q5 scoring
    partner_skill: str                    # unstructured prose
    combination_rank: int                 # drives breeding derivation — §5
```

### 3.3 SpawnZone

```python
@dataclass(frozen=True)
class SpawnZone:
    pal_id: str
    region: str
    coord: Coord                  # zone centroid
    time_of_day: TimeOfDay        # DAY | NIGHT | ANY
    is_alpha: bool
    rarity: SpawnRarity
```

## 4. Tier 2 entities

### 4.1 Element matrix and bosses (Q5)

```python
class Element(StrEnum):
    NEUTRAL = "neutral"; FIRE = "fire"; WATER = "water"; GRASS = "grass"
    ELECTRIC = "electric"; ICE = "ice"; GROUND = "ground"; DARK = "dark"
    DRAGON = "dragon"

# attacker → defender → damage multiplier
ElementMatrix = dict[Element, dict[Element, float]]

@dataclass(frozen=True)
class Boss:
    boss_id: str
    name: str                     # e.g. "Zoe & Grizzbolt"
    kind: BossKind                # TOWER | FIELD_ALPHA | RAID
    elements: list[Element]
    level: int
    coord: Coord | None
    tower_order: int | None       # progression position, enables "the next tower"
```

The matrix is small and fully enumerable — nine elements, exact multipliers. Q5 scoring is
therefore exact arithmetic, not estimation:

```python
def score(pal: OwnedPal, boss: Boss, matrix: ElementMatrix) -> float:
    offence = max(matrix[e][d] for e in pal.elements for d in boss.elements)
    defence = max(matrix[d][e] for d in boss.elements for e in pal.elements)
    level_ratio = pal.level / boss.level
    return offence / defence * level_ratio * pal.effective_attack
```

The exact formula is calibration work (Phase 3), but its **shape** is fixed: deterministic,
inspectable, and reproducible. The LLM never computes it.

`tower_order` exists specifically so conversation memory can resolve *"what about the next
tower?"* ([ADR-0013](adr/0013-conversation-memory.md)).

### 4.2 Technology tree (Q6)

```python
@dataclass(frozen=True)
class TechNode:
    tech_id: str
    name: str
    required_level: int
    tech_points: int
    ancient_points: int           # separate currency for ancient tech
    prerequisites: list[str]      # tech_ids
    category: TechCategory        # BASE | GEAR | WEAPON | PAL_GEAR | INFRA
    unlocks: list[str]            # prose description of what it grants
```

Q6 candidate generation is set arithmetic:

```
available = {t for t in tech
             if t.required_level <= player.level
             and t.tech_id not in player.unlocked
             and set(t.prerequisites) <= player.unlocked}
```

The candidate set is computed. The LLM ranks and explains it against a stated goal, and
may not add to it.

### 4.3 BaseSite (Q4)

```python
@dataclass(frozen=True)
class BaseSite:
    site_id: str
    coord: Coord
    flatness_score: float           # hand-curated, 0-1
    nearby_resources: list[ResourceType]
    has_water: bool
    recommended_slot: list[int]
    rationale: str                  # curated prose — Q4 synthesis input
    source_attribution: str
```

## 5. Breeding model (Q3)

Palworld breeding is understood to be deterministic from a per-Pal **combination rank**:
the child of two parents is the Pal whose rank is closest to the parents' average, subject
to an override table of special-case pairs.

If this holds, the graph compresses to **one integer per Pal plus an exception table** —
a few hundred rows from which every edge is derived on demand, versus scraping tens of
thousands of explicit pairs.

This is assumption **A3** and is unverified. It is isolated behind a protocol so failure
costs ingestion effort rather than a redesign:

```python
class BreedingModel(Protocol):
    def child_of(self, a: PalName, b: PalName) -> PalName: ...
    def parents_producing(self, target: PalName) -> Iterator[tuple[PalName, PalName]]: ...
```

`RankBasedBreedingModel` is primary; `TableBasedBreedingModel` is the fallback.
`breeding_path` runs BFS against the protocol only and is unchanged either way.
See [ADR-0008](adr/0008-breeding-graph-derivation.md).

## 6. Knowledge corpus (Tier 3)

```python
@dataclass(frozen=True)
class KnowledgeChunk:
    chunk_id: str
    text: str
    embedding: list[float]
    source_title: str
    source_url: str
    section: str | None
    patch_version: str            # staleness is visible, not silent
    entities: list[str]           # canonical names mentioned — enables hybrid retrieval
```

**Chunking.** Split on semantic boundaries (headings, sections) rather than fixed token
windows. Game wiki content is already sectioned; respecting that structure keeps chunks
self-contained and citations meaningful.

**Retrieval is hybrid.** Vector similarity alone underperforms on queries naming a specific
entity — *"what does Artisan do"* should strongly prefer chunks whose `entities` include
`Artisan`. Combine similarity with an entity-match boost, using the same canonical names
the lexicon produces.

**Grounding is mandatory.** If no chunk clears the relevance threshold, the answer is
*"not in my sources"*. Every Tier 3 card carries `source_title` and `source_url`.
See [ADR-0011](adr/0011-corpus-grounded-knowledge.md).

**Index.** A few thousand chunks. Exact search over stored vectors is sub-millisecond at
this size — no ANN structure, no external service, no index build step. Revisit only if
the corpus grows by orders of magnitude.

## 7. Runtime state

Derived from the save file; never persisted by this application.

```python
@dataclass(frozen=True)
class PlayerState:
    player_level: int
    owned_pals: list[OwnedPal]      # pal_id, level, traits, stats
    bases: list[Base]               # slot, coord
    unlocked_tech: list[TechId]     # assumption A6
    last_parsed: datetime
    source_path: Path
```

```python
@dataclass
class ConversationTurn:
    user_id: str
    tool_called: str | None
    entities: dict[str, str]        # resolved canonical entities
    result_summary: str             # compact, not the full card
    timestamp: datetime
```

Conversation memory stores **resolved state, not raw transcripts** — follow-ups resolve
against structured facts, and the router's context window stays small.

## 8. The entity lexicon

Load-bearing infrastructure serving three consumers from one source of truth:

1. **STT keyterm boosting** — biases the acoustic model toward Palworld proper nouns
2. **Fuzzy correction** — maps mangled tokens to canonical names
3. **LLM enum constraints** — generates the tool schemas' entity parameters

It now also feeds a fourth: **corpus entity tagging** (§6), so retrieval and routing share
one vocabulary.

```json
{
  "pals": [
    { "canonical": "Lifmunk",
      "aliases": ["life monk", "lif munk", "liftmunk"],
      "phonetic": "LFMNK" }
  ],
  "bosses": [
    { "canonical": "Zoe & Grizzbolt",
      "aliases": ["zoe and grizzbolt", "zoe", "grizzbolt", "first tower"],
      "phonetic": "S GRSBLT" }
  ],
  "resources": [
    { "canonical": "coal", "aliases": ["cole", "kohl"], "phonetic": "KL" }
  ]
}
```

Aliases are seeded by hand and **grown from observed STT failures** — every misrecognition
becomes a permanent alias, making A5 tractable rather than open-ended.

Matches below threshold are **not** silently coerced: the card names the unrecognized token
so the player can retry, rather than answering a question that was never asked.

## 9. Versioning

Data directories are keyed by patch version. A patch means a new directory and a re-run of
ingestion; previous versions stay for diffing and rollback. `patch_version` on corpus
chunks makes stale prose visible at the card level, and `/palintel status` reports the
loaded version — so a stale answer is always diagnosable.
