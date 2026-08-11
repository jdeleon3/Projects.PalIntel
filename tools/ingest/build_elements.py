"""Build the element dataset: Pal typing from the pak, the effectiveness matrix from the wiki.

Input : data/raw/pal_monster_parameter.json  (PakExtract.exe tables - ElementType1/2)
        data/raw/elements_wiki.md            (palworld.wiki.gg/wiki/Elements, cached)
        data/<version>/lexicon.json
Output: data/<version>/elements.json

**The two halves have different provenance and the file says so per field.** Pal typing
is Tier 1 fact from the game's own tables. The effectiveness matrix is not in the game's
tables *at all*: all **530** data tables in the pak were listed and searched, and the only
matches for element/damage/affinity are `DT_PalAwakeningItemElement` (an item table) and
`DT_PlayerDamageCameraShakeTable`. So the matrix lives in code or blueprint, exactly like
the ranch mapping ([ADR-0014](../../Docs/adr/0014-game-files-as-source.md) amendment), and
the wiki is the source. This is the project's **second** community-sourced dataset and it
is scoped the same way: the facts it cannot get from the game are marked, not laundered.

**What makes that safe here is that the matrix is checkable against itself.** Effectiveness
is an involution - if Dark is strong against Neutral then Neutral is weak against Dark -
so a transcription error breaks the pairing. `validate()` enforces that, plus the arity
rules the page states in prose (exactly one weakness each, at most two strengths, exactly
one element with two). A hand-copied 9x9 table with no check is a claim; one that fails
closed on its own structure is a dataset.

The wiki and the pak also disagree about element *names* - Neutral/Grass/Electric/Ground
against Normal/Leaf/Electricity/Earth. Aliased explicitly below rather than matched
fuzzily, for the same reason `build_ranch.py` writes out Woolipop: a matcher loose enough
to join those pairs would also join things that must stay apart.

Usage: python tools/ingest/build_elements.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

SOURCE_URL = "https://palworld.wiki.gg/wiki/Elements"

# Wiki display name -> the enum name every pak table uses. Four of nine differ.
ALIASES = {"neutral": "Normal", "grass": "Leaf", "electric": "Electricity",
           "ground": "Earth"}

# Multipliers, stated on the page. Kept here rather than in the consumer so the
# dataset is self-describing: a card that says "2x" and a scorer that assumes 1.5
# would disagree silently.
STRONG, WEAK, NEUTRAL_MULT = 2.0, 0.5, 1.0

ROW = re.compile(r"^\|\s*\[([^\]]+)\][^|]*\|([^|]*)\|([^|]*)\|\s*$")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")


def canon(name: str) -> str:
    n = name.strip()
    return ALIASES.get(n.lower(), n)


def parse_wiki(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line.strip())
        if not m or m.group(1).strip() == "Element":
            continue
        element, strong_cell, weak_cell = m.groups()
        out[canon(element)] = {
            "strong_against": [canon(x) for x in LINK.findall(strong_cell)],
            "weak_against": [canon(x) for x in LINK.findall(weak_cell)],
        }
    return out


def validate(matrix: dict[str, dict], pak_elements: set[str]) -> list[str]:
    """Every way this table can be wrong that the table itself can reveal."""
    errs = []
    missing = pak_elements - set(matrix)
    if missing:
        errs.append(f"elements in the pak with no matrix row: {sorted(missing)}")
    extra = set(matrix) - pak_elements
    if extra:
        errs.append(f"matrix rows naming no pak element: {sorted(extra)}")

    for name, row in matrix.items():
        # The page states this in prose: one weakness each, zero to two strengths.
        if len(row["weak_against"]) != 1:
            errs.append(f"{name}: {len(row['weak_against'])} weaknesses, expected 1")
        if len(row["strong_against"]) > 2:
            errs.append(f"{name}: {len(row['strong_against'])} strengths, expected <= 2")
        if name in row["strong_against"] or name in row["weak_against"]:
            errs.append(f"{name}: matched against itself")

    # The involution. A strong against B has to mean B weak against A, or one of the
    # two cells was mistyped - which is the failure mode of copying a 9x9 grid by hand.
    for name, row in matrix.items():
        for target in row["strong_against"]:
            if name not in matrix.get(target, {}).get("weak_against", []):
                errs.append(f"{name} strong vs {target}, but {target} not weak vs {name}")
        for source in row["weak_against"]:
            if name not in matrix.get(source, {}).get("strong_against", []):
                errs.append(f"{name} weak vs {source}, but {source} not strong vs {name}")

    two = [n for n, r in matrix.items() if len(r["strong_against"]) == 2]
    if len(two) != 1:
        errs.append(f"the page says exactly one element is strong against two; found {two}")
    return errs


def build(version: str) -> dict:
    mp = json.loads((RAW / "pal_monster_parameter.json").read_text(encoding="utf-8"))
    rows = (mp[0] if isinstance(mp, list) else mp)["Rows"]
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))
    name_of = {i.lower(): p["canonical"]
               for p in lexicon["pals"] for i in p["internal_ids"]}

    def enum(v) -> str | None:
        v = (v or "").rsplit("::", 1)[-1]
        return None if v in ("", "None") else v

    pals, pak_elements = [], set()
    for cid, r in rows.items():
        types = [e for e in (enum(r.get("ElementType1")), enum(r.get("ElementType2"))) if e]
        if not types:
            continue
        pak_elements.update(types)
        pals.append({"character_id": cid, "name": name_of.get(cid.lower()),
                     "elements": types})

    matrix = parse_wiki(RAW / "elements_wiki.md")
    errors = validate(matrix, pak_elements)

    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "mixed - see per-field notes",
        "typing_source": "DT_PalMonsterParameter ElementType1/ElementType2 (pak)",
        "matrix_source": SOURCE_URL,
        "matrix_note": "No element-effectiveness table exists in the pak. All 530 data "
                       "tables were listed and searched; the only element/damage matches "
                       "are DT_PalAwakeningItemElement (items) and "
                       "DT_PlayerDamageCameraShakeTable. So the matrix is community-"
                       "sourced, like ranch outputs, and is validated against its own "
                       "structure rather than trusted: effectiveness is an involution, "
                       "so a mistyped cell breaks the pairing and fails this build.",
        "multipliers": {"strong": STRONG, "weak": WEAK, "neutral": NEUTRAL_MULT},
        "dual_element_note": "A skill both strong and weak against a dual-element Pal is "
                             "1x, not 2x or 0.5x. The page states 4x and 0.25x do not "
                             "occur, because no two elements share a weakness.",
        "validation_errors": errors,
        "stats": {
            "elements": len(matrix),
            "pals_typed": len(pals),
            "dual_element": sum(1 for p in pals if len(p["elements"]) == 2),
            "pals_without_a_name": sum(1 for p in pals if not p["name"]),
        },
        "matrix": dict(sorted(matrix.items())),
        "pals": sorted(pals, key=lambda p: p["character_id"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    for needed, how in ((RAW / "pal_monster_parameter.json",
                         "dotnet run --project tools/extract/PakExtract -- tables"),
                        (RAW / "elements_wiki.md",
                         f"defuddle parse {SOURCE_URL} --md -o {RAW / 'elements_wiki.md'}")):
        if not needed.exists():
            sys.exit(f"Missing {needed}\n  {how}")

    data = build(args.version)
    if data["validation_errors"]:
        # Fail closed. A silently wrong matrix produces confidently wrong counter advice,
        # which is the one output this project refuses to ship.
        print("element matrix FAILED validation:")
        for e in data["validation_errors"]:
            print(f"  {e}")
        sys.exit(1)

    dest = REPO / "data" / args.version / "elements.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"elements -> {dest}")
    print(f"  elements         {s['elements']}  (matrix validated: involution + arity)")
    print(f"  pals typed       {s['pals_typed']}  ({s['dual_element']} dual-element)")
    if s["pals_without_a_name"]:
        print(f"  typed but unnamed in the lexicon: {s['pals_without_a_name']}")


if __name__ == "__main__":
    main()
