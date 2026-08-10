"""Build the ranch production dataset from the community wiki, validated against the pak.

Input : data/raw/ranch_wiki.md      (palworld.wiki.gg/wiki/Ranch, cached)
        data/raw/ranch_roster.json  (PakExtract.exe ranch - the authoritative roster)
        data/<version>/lexicon.json
Output: data/<version>/ranch_drops.json

**This is the project's only dataset whose facts come from a community site rather than
the game files, and that is a deliberate, scoped exception to
[ADR-0014](../../Docs/adr/0014-game-files-as-source.md).** The reason is recorded in the
ranch spike (Docs/04-roadmap.md): all 284 data tables were enumerated and none maps a Pal
to its ranch output. The mapping lives in blueprint bytecode, which property extraction
does not reach. Finding an authoritative in-game source stays on the backlog.

What keeps this honest is a cross-check against the pak's `BP_Action_SpawnItem_*` assets.
**That check is asymmetric, and the asymmetry was measured rather than assumed.** The
roster contains Snock, Teafant, Direhowl and Tarantriss, none of which is a ranch Pal -
so the asset means "has an item-spawning action", which is broader than "is ranchable".
Consequently:

  * a wiki row whose Pal is NOT on the roster is genuinely suspicious, since the roster
    is the wider set - it is published with `roster_verified: false` rather than dropped
    or silently trusted;
  * a roster entry with no wiki row is **weak** evidence of a gap, because the roster
    over-approximates. Reported, not treated as a coverage failure.

One naming inconsistency is the game's own: the parameter and name tables both spell
Woolipop `SweetsSheep`, while its action asset is `BP_Action_SpawnItem_SweetSheep`.
Aliased explicitly below rather than matched fuzzily - a normalisation loose enough to
join those two would also join names that should stay apart.

Usage: python tools/ingest/build_ranch.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

SOURCE_URL = "https://palworld.wiki.gg/wiki/Ranch"

# Asset name -> the id every other table uses. The game ships Woolipop's spawn-item
# action as `SweetSheep` and everything else about it as `SweetsSheep`. One known case,
# written down rather than absorbed into a fuzzy matcher.
ROSTER_ALIASES = {"sweetsheep": "sweetssheep",
                  "sweetsheep_ground": "sweetssheep_ground"}

# A table row: | [Pal](link) | No. | [Item](link), [Item](link) | count | food |
ROW = re.compile(r"^\|\s*\[([^\]]+)\]\([^)]*\)[^|]*\|\s*([0-9]+[A-Z]?)\s*\|(.*?)\|"
                 r"\s*([0-9]+)\s*\|\s*([0-9]+)\s*\|\s*$")
LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# "(x10)", "(80%)" - quantity and probability qualifiers the wiki appends to an item.
QUALIFIER = re.compile(r"\(\s*(?:x\s*([0-9]+)|([0-9]+)\s*%)\s*\)")


def parse_wiki(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        pal, deck_no, drops_cell, count, food = m.groups()
        # Footnote markers ride along with the name: "Vixy [^1]".
        pal = re.sub(r"\s*\[\^\d+\]\s*", "", pal).strip()

        # Walk the links in order and read each one's qualifier out of the gap before
        # the next link. Splitting the cell on ")," instead ate the closing paren of
        # every link, so LINK stopped matching and Vixy's seven drops parsed as two.
        drops = []
        links = list(LINK.finditer(drops_cell))
        for i, link in enumerate(links):
            tail_end = links[i + 1].start() if i + 1 < len(links) else len(drops_cell)
            qual = QUALIFIER.search(drops_cell[link.end():tail_end])
            drops.append({
                "item": link.group(1).strip(),
                "stack": int(qual.group(1)) if qual and qual.group(1) else 1,
                "chance_percent": int(qual.group(2)) if qual and qual.group(2) else None,
            })
        if drops:
            rows.append({"pal": pal, "deck_no": deck_no, "drops": drops,
                         "per_cycle": int(count), "food": int(food)})
    return rows


def build(version: str) -> dict:
    wiki = parse_wiki(RAW / "ranch_wiki.md")
    roster = json.loads(
        (RAW / "ranch_roster.json").read_text(encoding="utf-8"))["character_ids"]
    lexicon = json.loads(
        (REPO / "data" / version / "lexicon.json").read_text(encoding="utf-8"))

    to_internal = {p["canonical"].lower(): p["internal_ids"] for p in lexicon["pals"]}
    roster_set = {ROSTER_ALIASES.get(r.lower(), r.lower()) for r in roster}

    entries, unmatched, off_roster = [], [], []
    for row in wiki:
        ids = to_internal.get(row["pal"].lower())
        if not ids:
            # Named on the wiki, absent from the lexicon: a rename, a typo, or content
            # newer than the extracted game version. Not silently dropped.
            unmatched.append(row["pal"])
            continue
        hit = next((i for i in ids if i.lower() in roster_set), None)
        if hit is None:
            off_roster.append(f"{row['pal']} ({'/'.join(ids)})")
        entries.append({
            "pal": row["pal"],
            "internal_id": hit or ids[0],
            # The wiki says this Pal can be ranched and the game's own asset list does
            # not corroborate it. Published, because the roster is not a complete
            # authority either - but never as a verified fact.
            "roster_verified": hit is not None,
            "drops": row["drops"],
            "per_cycle": row["per_cycle"],
            "food": row["food"],
        })

    covered = {ROSTER_ALIASES.get(e["internal_id"].lower(), e["internal_id"].lower())
               for e in entries}
    missing = sorted(r for r in roster
                     if ROSTER_ALIASES.get(r.lower(), r.lower()) not in covered)

    return {
        "dataset_version": 1,
        "game_version": version,
        "provenance": "community-wiki",
        "source": SOURCE_URL,
        "source_note": "Ranch OUTPUT items are not present in any extractable game "
                       "table (Docs/04-roadmap.md, ranch spike), so they are sourced "
                       "from the community wiki. The ROSTER below is from the pak and "
                       "validates the wiki's Pal list. Finding an authoritative "
                       "in-game source for the items is on the backlog.",
        "roster_source": "BP_Action_SpawnItem_<CharacterID> assets in Pal-Windows.pak",
        "roster_note": "A BP_Action_SpawnItem_* asset means the Pal has an item-spawning "
                       "action, which is broader than being ranchable - the roster "
                       "includes Snock, Teafant, Direhowl and Tarantriss. So a roster "
                       "entry with no wiki row is weak evidence of a gap, while a wiki "
                       "row off the roster is a real flag.",
        "stats": {
            "wiki_rows": len(wiki),
            "published": len(entries),
            "roster_verified": sum(e["roster_verified"] for e in entries),
            "roster_size": len(roster),
            "roster_without_wiki_row": len(missing),
            "wiki_not_in_lexicon": len(unmatched),
            "wiki_not_on_roster": len(off_roster),
        },
        "entries": sorted(entries, key=lambda e: e["pal"]),
        "roster_without_wiki_row": missing,
        "wiki_not_in_lexicon": sorted(unmatched),
        "wiki_not_on_roster": sorted(off_roster),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    for needed, how in ((RAW / "ranch_wiki.md", f"cache {SOURCE_URL} there"),
                        (RAW / "ranch_roster.json",
                         "dotnet run --project tools/extract/PakExtract -- ranch")):
        if not needed.exists():
            sys.exit(f"Missing {needed}\n  {how}")

    data = build(args.version)
    dest = REPO / "data" / args.version / "ranch_drops.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    s = data["stats"]
    print(f"ranch drops -> {dest}")
    print(f"  wiki rows parsed     {s['wiki_rows']}")
    print(f"  published            {s['published']}"
          f"  ({s['roster_verified']} corroborated by the pak roster)")
    for label, key in (("roster entries with no wiki row (weak signal)",
                        "roster_without_wiki_row"),
                       ("wiki Pals not in lexicon", "wiki_not_in_lexicon"),
                       ("wiki Pals NOT on the roster - flagged, not dropped",
                        "wiki_not_on_roster")):
        rows = data[key]
        if rows:
            print(f"  {label}: {len(rows)}")
            print(f"    {', '.join(rows)}")


if __name__ == "__main__":
    main()
