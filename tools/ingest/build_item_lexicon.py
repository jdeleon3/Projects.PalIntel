"""Build a SECOND, separate lexicon over item names — for the `item_source` fast path.

Input : data/<version>/pal_drops.json  (by_item keys)
        data/<version>/recipes.json    (by_item keys)
Output: data/<version>/item_lexicon.json

**Deliberately not folded into lexicon.json.** That file's `Lexicon.rank()` returns one
ranked candidate list shared by Pals, resources and tower leaders, and item names are
disproportionately ordinary English — `Bone`, `Egg`, `Wood`, `Milk`, `Ore`, `Coal` are
canonical item names here, and 12 of the 18 resources already share a display name with a
droppable or craftable item (`Coal`, `Ore`, `Red Berries`...). Adding 1,031 more surfaces
to the one list every utterance is ranked against — including utterances that name no item
at all — is exactly the pollution `item_source` was kept out of the lexicon to avoid
(see `build_pal_drops.py`'s item-source note and Docs/adr/0007-entity-lexicon-boundary.md).

A second `Lexicon` instance, loaded from a file that carries only `items`, keeps that risk
contained: `palintel.routing.StubRouter._item_call` ranks against it ONLY when an
item-shaped cue ("who drops X", "what do I need for X") has already fired, and Pal/resource
ranking never sees an item candidate at all.

**No seeded aliases.** Every alias in `build_lexicon.py`'s `SEED_ALIASES` was harvested from
a recording of misheard speech; none of these item names has ever been recorded being
spoken to this bot, so there is nothing to seed from yet. Aliases here are spacing/
punctuation variants only (`variants()`, imported), same as the tower leaders got on day
one — they grow from observed failures, same as everything else in this project's lexicon.

Usage: python tools/ingest/build_item_lexicon.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_lexicon import metaphone_key, safe_aliases, variants  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def build(version: str) -> dict:
    base = REPO / "data" / version
    drop_path = base / "pal_drops.json"
    recipe_path = base / "recipes.json"
    if not drop_path.exists() and not recipe_path.exists():
        sys.exit(f"neither {drop_path} nor {recipe_path} exists - run build_pal_drops.py "
                 "and/or build_recipes.py first")

    names: set[str] = set()
    if drop_path.exists():
        names |= set(json.loads(drop_path.read_text(encoding="utf-8"))["by_item"])
    if recipe_path.exists():
        names |= set(json.loads(recipe_path.read_text(encoding="utf-8"))["by_item"])

    items = [{
        "canonical": name,
        "aliases": sorted(set(safe_aliases(variants(name)))),
        "phonetic": metaphone_key(name),
    } for name in sorted(names)]

    return {
        "lexicon_version": 1,
        "game_version": version,
        "source": "pal_drops.json + recipes.json, union of by_item keys",
        "notes": (
            "A SEPARATE lexicon from lexicon.json - see the module docstring. Loaded "
            "into its own Lexicon instance and ranked only inside "
            "StubRouter._item_call, never merged into the Pal/resource candidate list."
        ),
        "stats": {"items": len(items)},
        "items": items,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    data = build(args.version)
    dest = REPO / "data" / args.version / "item_lexicon.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"item lexicon -> {dest}")
    print(f"  items {data['stats']['items']}")


if __name__ == "__main__":
    main()
