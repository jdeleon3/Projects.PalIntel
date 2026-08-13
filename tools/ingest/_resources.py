"""Which spawner class yields which resource, derived from the game's own tables.

Shared by build_lexicon.py and build_resource_nodes.py, and it has to be: the lexicon is
what the router's enum and the STT hotword list are built from, and the node dataset is
what the answers come out of. A resource in one and not the other is a query that
resolves to an entity with no data, or a node nobody can name.

They cannot simply read each other's output - the lexicon is an input to the Pal spawn
ingest, which is an input to the node ingest - so the derivation is shared instead.

The old approach was a hand-written class -> resource map, and reading blueprint names
got two of its six entries wrong: BP_PalMapObjectSpawner_SkyIslandOre_C yields Soralite
and _WorldTreeOre_C yields Paloxite, neither of which is Ore, and both shipped as `ore`
through the whole of Phase 1. `_RockIron_C` would have been guessed as iron; it yields
Pure Quartz.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# What counts as a locatable resource, by the game's own item category rather than by
# opinion. Ore, stone and wood are the mined and logged materials; FoodVegetable adds
# berries and mushrooms, which are gathered from placed nodes in exactly the same way and
# which players ask for by name. Everything the cut excludes is a collectible rather than
# a material: the stat lotuses, Dog Coins, Beautiful Flowers, Kinship Peaches.
LOCATABLE_CATEGORIES = {"MaterialOre", "MaterialStone", "MaterialWood", "FoodVegetable"}

# Canonical ids are slugged from the item's English name so the lexicon and the cards
# agree by construction. One exception, to keep continuity with everything Phase 1
# measured: item `Quartz` displays as "Pure Quartz", and the recorded evaluation set, the
# lexicon aliases and the A5 transcripts all say "quartz".
CANONICAL_OVERRIDE = {"Quartz": "quartz"}

# The four Phase 1 resources. Asserted by both callers: if a rebuild stops producing
# these ids, the eval set and the lexicon silently stop lining up with the data.
PHASE1_RESOURCES = {"ore", "coal", "sulfur", "quartz"}

# Recognised but not placed: in the lexicon so the player can name it, out of the node
# dataset because nothing places it. `cards.NOT_PLACED` renders the difference.
#
# **Empty since 2026-08-12.** Its only entry was crude oil, on the reasoning that it "has
# no overworld spawner class - it comes from oil rigs". It has 185 placements of
# `BP_LevelObject_OilField_C`, whose CDO names `CrudeOil` outright; the derivation below
# never saw them because it reads `BP_PalMapObjectSpawner*` and an oil field is a
# `BP_LevelObject`. **An absence in a filtered search was written down as a fact about the
# world**, and a card published it. Crude Oil's `type_b` is `MaterialOre`, so it now
# derives as locatable with no help at all - which is the test that the fix is a widening
# and not a second special case.
UNPLACED_RESOURCES: dict[str, str] = {}


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def item_ids(root: Path | None = None) -> dict[str, str]:
    """canonical resource id -> the game item id it drops (`Coal`, `Pal_crystal_S`).

    Same chain as `derive`, re-walked rather than cached alongside it, so the two cannot
    be updated apart. The item id is what joins a resource to its inventory icon, and it
    is emphatically NOT the canonical id: `quartz` is `Quartz` but `ore` is `CopperOre`
    and `paldium_fragment` is `Pal_crystal_S`.
    """
    raw = root or RAW
    items = json.loads((raw / "items.json").read_text(encoding="utf-8"))
    drops = json.loads((raw / "node_drops.json").read_text(encoding="utf-8"))

    out: dict[str, str] = {}
    for entry in drops:
        if not entry.get("drops"):
            continue
        primary = max(entry["drops"], key=lambda d: d["Num"])["StaticItemId"]["Key"]
        item = items.get(primary)
        if not item or not item.get("name"):
            continue
        if item.get("type_b") not in LOCATABLE_CATEGORIES:
            continue
        out[CANONICAL_OVERRIDE.get(primary) or slug(item["name"])] = primary
    return out


def derive(root: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """(spawner class -> canonical id, canonical id -> English display name).

    The chain is entirely in the data and PakExtract walks it (`dotnet run -- drops`):

        spawner CDO -> MapObjectId -> master table -> map object -> DropItems[]

    A node with several drops is named by its LARGEST one. A stone rock also yields a
    little paldium and copper, and letting it answer "where's paldium" would bury the
    2,062 dedicated paldium nodes under 12,445 rocks that mostly are not.
    """
    raw = (root or RAW)
    items = json.loads((raw / "items.json").read_text(encoding="utf-8"))
    drops = json.loads((raw / "node_drops.json").read_text(encoding="utf-8"))

    mapping: dict[str, str] = {}
    display: dict[str, str] = {}
    for entry in drops:
        if not entry.get("drops"):
            continue
        primary = max(entry["drops"], key=lambda d: d["Num"])["StaticItemId"]["Key"]
        item = items.get(primary)
        if not item or not item.get("name"):
            continue
        if item.get("type_b") not in LOCATABLE_CATEGORIES:
            continue
        canonical = CANONICAL_OVERRIDE.get(primary) or slug(item["name"])
        mapping[entry["cls"]] = canonical
        display[canonical] = item["name"]

    missing = PHASE1_RESOURCES - set(display)
    if missing:
        raise SystemExit(
            f"ABORT: the derived mapping no longer produces {sorted(missing)}. Every "
            "recorded evaluation and the lexicon's aliases are written against those ids.")
    return mapping, display


def provided(root: Path | None = None) -> set[str]:
    """Canonical ids you cannot mine - you build a structure on the spot instead.

    A `BP_PalMapObjectSpawner` is swung at with a pickaxe; a `BP_LevelObject` item
    provider is something you place a machine on. Both are fixed positions yielding a
    material, which is why they share a dataset, and the difference still has to reach the
    card: a coordinate on its own says "come here and mine it", and for crude oil the
    game's own answer is "install a Crude Oil Extractor in an oil field".

    Told apart by the ABSENCE of a master row - a mined node has `material_type`, which is
    the tool category, and a provider has none - rather than by a list of class names, so
    the distinction survives a patch adding another provider.
    """
    raw = (root or RAW)
    items = json.loads((raw / "items.json").read_text(encoding="utf-8"))
    drops = json.loads((raw / "node_drops.json").read_text(encoding="utf-8"))

    out: set[str] = set()
    for entry in drops:
        if not entry.get("drops") or entry.get("material_type") is not None:
            continue
        primary = max(entry["drops"], key=lambda d: d["Num"])["StaticItemId"]["Key"]
        item = items.get(primary)
        if not item or not item.get("name"):
            continue
        if item.get("type_b") not in LOCATABLE_CATEGORIES:
            continue
        out.add(CANONICAL_OVERRIDE.get(primary) or slug(item["name"]))
    return out
