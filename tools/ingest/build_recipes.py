"""Build the "what do I need to craft X" dataset.

Input : data/raw/tables/DT_ItemRecipeDataTable.json  (Product_Id, up to 5 materials each)
        data/raw/items.json                          (item id -> display name)
Output: data/<version>/recipes.json

**This is the recipe half of the "where can I find cakes" defect.** `item_source`
answers "what drops this" from `pal_drops.json`'s `by_item`, and for a crafted item that
is true and useless: *"Cake comes from - Lovander | 1 | 1%"* is a real drop row and not
what the player needed, because Cake is made at a Cooking Pot from Flour, Red Berries,
Milk, Egg and Honey. The table backing that fact was extracted and sitting in
`data/raw/tables/` unread for one full session before this ingest existed - the fourth
instance of this project's own recurring pattern (see `bosses.json`'s null levels,
`DT_ItemRecipeDataTable` itself before this file, `all_boss_landmarks.csv`).

**506 of 1,414 rows do not resolve to a real item and are excluded, not guessed at.**
Their `Product_Id`s are weapon/armour upgrade tiers (`Sword_2`..`Sword_5`,
`HeadEquip031_4`) that consume materials to enhance an item already owned rather than to
craft a new one, and none of them has its own entry in `items.json` - there is nothing to
call the product. Publishing "how do I make a Sword_4" from a display name this project
invented would be exactly the fabricated-value failure `build_pal_drops.py`'s zero-rate
rule exists to prevent, one field over.

**A product id is not a recipe id.** Several distinct table rows share one `Product_Id` -
`Pal_crystal_S`, `Pal_crystal_S_2` and eleven `CryStal_PalSphere*` rows all produce
Paldium Fragment, because breaking down a higher-tier Sphere is a second way to get one.
`CarbonFiber2` and both `PalUpgradeStone` pairs are the same shape. Collapsing them to one
entry per product would keep an arbitrary row and silently drop the others being real
alternative recipes; `by_item` therefore holds a LIST, cheapest (lowest work amount) first,
same ordering principle `build_pal_drops.py` uses for droppers.

**A recipe is published whole or not at all.** If any one of its materials fails to
resolve to a display name, the entire row is dropped rather than shown with a hole in it -
a 4-of-5 ingredient list reads as complete and is not, which is worse than the row simply
being absent. Measured: this never actually happens here, because the same case-fold that
recovers `stone`/`cloth`/`wood`/`Fiber` as materials (the pak's own casing is inconsistent,
as it is in the drop table) resolves every material that has any entry at all; the check
stays in the code because a future patch is not guaranteed to keep that true.

`UnlockItemID` - a schematic ("Blueprint_...") gating some recipes - is carried through
raw rather than surfaced on a card: roughly half of its 586 non-null values have no
display name of their own (`Blueprint_Salvage_FishingBait_1_A`), and even where one
resolves, which Pal or chest yields that schematic is a different, unringested fact. A
card that named the requirement without saying where to get it would be the same "true
and useless" failure this file exists to fix.

Usage: python tools/ingest/build_recipes.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

# Same placeholder set build_pal_drops.py filters on - untranslated or cut rows are not
# names a player could ask for.
PLACEHOLDER_NAMES = {"", "None", "en Text", "ja_text", "en_text"}

# How many alternative recipes to name on a card before it becomes a wall. Paldium
# Fragment has 13 rows; nobody needs to read all of them to craft a Cake.
MAX_RECIPES_SHOWN = 3


def build(version: str) -> dict:
    table = json.loads(
        (RAW / "tables" / "DT_ItemRecipeDataTable.json").read_text(encoding="utf-8"))
    items = json.loads((RAW / "items.json").read_text(encoding="utf-8"))
    # Case-insensitive fallback, same reasoning as build_pal_drops.py's `by_internal`:
    # the recipe table's own casing disagrees with the item table's on six material rows
    # (`stone`, `cloth`, `cloth2`, `FIber`, `wood`) and an exact match would drop them as
    # unresolved rather than recognise the same id spelled differently.
    lower = {k.lower(): k for k in items}

    def display(item_id: str) -> str | None:
        key = item_id if item_id in items else lower.get(item_id.lower())
        name = (items.get(key) or {}).get("name") if key else None
        return None if name in PLACEHOLDER_NAMES or name is None else name

    by_item: dict[str, list[dict]] = defaultdict(list)
    products_unresolved = 0
    materials_unresolved_rows = 0

    for row_id, row in table.items():
        product = display(row["Product_Id"])
        if product is None:
            products_unresolved += 1
            continue

        materials: list[dict] = []
        complete = True
        for i in range(1, 6):
            mat_id = row[f"Material{i}_Id"]
            if mat_id == "None":
                continue
            mat_name = display(mat_id)
            if mat_name is None:
                complete = False
                break
            materials.append({"item": mat_name, "count": row[f"Material{i}_Count"]})
        if not complete:
            materials_unresolved_rows += 1
            continue
        if not materials:
            # A "recipe" with nothing consumed is not a crafting question anyone asks -
            # none observed today, guarded so a future patch cannot publish one silently.
            continue

        unlock = row.get("UnlockItemID")
        by_item[product].append({
            "product_count": row["Product_Count"],
            "materials": materials,
            "work_amount": row["WorkAmount"],
            # Raw id, not a display name - see the module docstring on why this is not
            # resolved further here.
            "unlock_item_id": None if unlock in (None, "None") else unlock,
            "source_row": row_id,
        })

    # Cheapest first within a product - the reader wants the easy way to make something,
    # not an exhaustive index of every way the game can produce it.
    for recipes in by_item.values():
        recipes.sort(key=lambda r: (r["work_amount"], r["source_row"]))

    return {
        "dataset_version": 1,
        "game_version": version,
        "source": "DT_ItemRecipeDataTable, extracted from Pal-Windows.pak",
        "rules": {
            "unresolvable_products": "excluded - 506 of 1,414 rows are weapon/armour "
                                     "upgrade tiers (Sword_2..Sword_5) with no entry of "
                                     "their own in items.json, so there is no display "
                                     "name to publish a recipe under",
            "multiple_recipes_per_item": "several rows can share one Product_Id (13 "
                                         "different ways to end up with a Paldium "
                                         "Fragment) - all are kept, cheapest work amount "
                                         "first, rather than collapsing to one",
            "incomplete_materials": "a row is dropped WHOLE if any one material fails to "
                                    "resolve, never published with a missing ingredient",
            "unlock_item_id": "carried as a raw id, not resolved to a name or a source - "
                              "see the module docstring",
        },
        "stats": {
            "products_with_recipes": len(by_item),
            "recipe_rows_published": sum(len(r) for r in by_item.values()),
            "products_unresolved": products_unresolved,
            "rows_dropped_incomplete_materials": materials_unresolved_rows,
            "rows_total": len(table),
        },
        "max_recipes_shown": MAX_RECIPES_SHOWN,
        "by_item": dict(sorted(by_item.items())),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    src = RAW / "tables" / "DT_ItemRecipeDataTable.json"
    if not src.exists():
        sys.exit(f"No {src}.\n  Run: dotnet run --project tools/extract/PakExtract -- tables")

    data = build(args.version)
    dest = REPO / "data" / args.version / "recipes.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"recipes -> {dest}")
    print(f"  products with a recipe   {s['products_with_recipes']}")
    print(f"  recipe rows published    {s['recipe_rows_published']}")
    print(f"  products unresolved      {s['products_unresolved']} of {s['rows_total']}")
    print(f"  rows dropped (materials) {s['rows_dropped_incomplete_materials']}")
    print()
    cake = data["by_item"].get("Cake")
    if cake:
        mats = ", ".join(f"{m['item']} x{m['count']}" for m in cake[0]["materials"])
        print(f"  Cake <- {mats}")


if __name__ == "__main__":
    main()
