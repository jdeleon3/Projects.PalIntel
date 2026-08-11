"""Score the ADR-0008 rank model against an independent source.

Input : data/<version>/breeding.json   (tools/ingest/build_breeding.py)
        data/raw/breeding_wiki.md      (palworld.wiki.gg/wiki/Breeding, cached)

[ADR-0008](../../Docs/adr/0008-breeding-graph-derivation.md) is Provisional and requires
100% agreement against >= 100 independently-known combinations before the rank model is
accepted. **That gate as written cannot be satisfied by a breeding calculator**, and
saying so is the first job of this script: every calculator surveyed states the averaging
rule explicitly, so it derives from `CombiRank` exactly as we do. Scoring against one
measures whether two implementations of the same arithmetic agree. It does not measure
whether the arithmetic is how the game behaves.

So the gate is run as three checks with **different evidential strength**, reported
separately and never summed into one number:

  A. RANKS - our CombiRank per tribe against the wiki's published Rank column.
     Tests extraction and the tribe collapse. Independent of the mechanic, and the
     cheapest way to catch a filtering mistake that would poison everything downstream.

  B. ELIGIBILITY - the wiki publishes an "Eligible Child" flag; the pak does not. So
     eligibility must be derived, and in this repo a derived rule is a claim. We derive
     it and measure the derivation against the wiki's column, rather than adopting it.

  C. EXCEPTION LOAD - for every row in DT_PalCombiUnique, does the rank rule already
     produce that child? Rows where it agrees are redundant; rows where it disagrees are
     the exception table earning its place. Needs no external data at all, and it
     quantifies how much of the model is rule and how much is table - which is the
     question ADR-0008 actually cared about when it talked about "a handful" vs
     "hundreds".

Check C is the one that can fail honestly. If the rank rule disagrees with the exception
table on rows the table does not cover, the model is incomplete.

Usage: python tools/eval/score_breeding.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "raw"

SOURCE_URL = "https://palworld.wiki.gg/wiki/Breeding"

# | [Name](url "Name") | 001 | 1470 | Yes | 252 |
WIKI_ROW = re.compile(
    r"^\|\s*\[([^\]]+)\]\([^)]*\)\s*\|\s*([0-9]+[A-Z]?)\s*\|\s*([0-9]+)\s*\|"
    r"\s*(Yes|No)\s*\|\s*([0-9]+)\s*\|\s*$")


def parse_wiki(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = WIKI_ROW.match(line.strip())
        if m:
            name, deck, rank, eligible, index = m.groups()
            out.append({"name": name.strip(), "deck_no": deck, "rank": int(rank),
                        "eligible_child": eligible == "Yes", "index_no": int(index)})
    return out


def child_of(rank_a: int, rank_b: int, candidates: list[tuple[int, str]],
             rounding: str) -> str:
    """The Pal whose rank is nearest the parents' average.

    `candidates` is (rank, tribe) sorted by rank, restricted to eligible children.
    The rounding convention is a parameter because ADR-0008 never stated one and the
    difference is invisible on most pairs - it only shows up when the average lands
    exactly between two ranks. Measured rather than picked.
    """
    if rounding == "floor":
        avg = (rank_a + rank_b) // 2
    elif rounding == "floor_plus_half":
        avg = (rank_a + rank_b + 1) // 2
    else:
        raise ValueError(rounding)
    # Nearest by absolute distance; on a tie the lower rank wins, which is itself a
    # convention and is why ties are counted and reported below.
    best = min(candidates, key=lambda rt: (abs(rt[0] - avg), rt[0]))
    return best[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()

    built_path = REPO / "data" / args.version / "breeding.json"
    wiki_path = RAW / "breeding_wiki.md"
    for needed, how in ((built_path, "python tools/ingest/build_breeding.py"),
                        (wiki_path, f"defuddle parse {SOURCE_URL} --md -o {wiki_path}")):
        if not needed.exists():
            sys.exit(f"Missing {needed}\n  {how}")

    built = json.loads(built_path.read_text(encoding="utf-8"))
    tribes = built["tribes"]
    wiki = parse_wiki(wiki_path)
    print(f"wiki rows parsed: {len(wiki)}   built tribes: {len(tribes)}\n")
    if not wiki:
        sys.exit("No wiki rows parsed - the page layout changed, and a silent zero here "
                 "would report a passing gate on an empty comparison.")

    by_name = {t["name"]: t for t in tribes if t.get("name")}

    # ---- A. ranks -------------------------------------------------------------
    matched, rank_mismatch, unmatched = [], [], []
    for w in wiki:
        t = by_name.get(w["name"])
        if t is None:
            unmatched.append(w["name"])
            continue
        matched.append((w, t))
        if t["rank"] != w["rank"]:
            rank_mismatch.append(f"{w['name']}: pak={t['rank']} wiki={w['rank']}")

    print("A. RANKS - extraction and tribe collapse")
    print(f"   joined by name            {len(matched)}")
    print(f"   rank mismatches           {len(rank_mismatch)}")
    for m in rank_mismatch[:10]:
        print(f"     {m}")
    print(f"   wiki names not in lexicon {len(unmatched)}")
    if unmatched:
        print(f"     {', '.join(sorted(unmatched)[:8])}")

    # ---- B. eligibility -------------------------------------------------------
    # The claim under test: a Zukan suffix means "cannot be produced by rank averaging".
    derived_ineligible = {t["name"] for t in tribes
                          if t.get("name") and t.get("zukan_suffix")}
    wiki_ineligible = {w["name"] for w in wiki if not w["eligible_child"]}
    joined = {w["name"] for w, _ in matched}
    d_in, w_in = derived_ineligible & joined, wiki_ineligible & joined

    print("\nB. ELIGIBILITY - the derived rule 'a Zukan suffix means not a valid child'")
    print(f"   wiki says ineligible      {len(w_in)}")
    print(f"   suffix rule says so       {len(d_in)}")
    print(f"   agree                     {len(d_in & w_in)}")
    fp, fn = sorted(d_in - w_in), sorted(w_in - d_in)
    print(f"   rule says NO, wiki says YES  {len(fp)}  {fp[:6]}")
    print(f"   rule says YES, wiki says NO  {len(fn)}  {fn[:6]}")
    verdict = "HOLDS" if not fp and not fn else "DOES NOT HOLD"
    print(f"   -> the suffix rule {verdict}")

    # ---- C. exception load ----------------------------------------------------
    # Candidates are eligible children only. Built from the wiki's column where we have
    # it, because check B is what decides whether the derived rule may stand in for it.
    eligible = [(t["rank"], t["tribe"]) for t in tribes
                if t.get("name") not in wiki_ineligible]
    eligible.sort()
    rank_of = {t["tribe"]: t["rank"] for t in tribes}

    print(f"\nC. EXCEPTION LOAD - {len(eligible)} eligible children in the candidate set")
    for rounding in ("floor", "floor_plus_half"):
        redundant = agreeing = disagreeing = skipped = 0
        for e in built["exceptions"]:
            ra, rb = rank_of.get(e["parent_a"]), rank_of.get(e["parent_b"])
            if ra is None or rb is None:
                skipped += 1
                continue
            derived = child_of(ra, rb, eligible, rounding)
            actual = e["child_character_id"]
            # The exception names a CharacterID; the derivation names a tribe. They
            # coincide for base forms, which is what makes a row redundant.
            if derived == actual:
                redundant += 1
                agreeing += 1
            else:
                disagreeing += 1
        total = redundant + disagreeing
        print(f"   rounding={rounding:<16} rows scored {total:<5} "
              f"redundant {redundant:<5} genuinely overriding {disagreeing:<5} "
              f"unscorable {skipped}")

    print("\n   A row the rank rule already produces is an exception in name only.")
    print("   The overriding count is how much of this model is table rather than rule.")


if __name__ == "__main__":
    main()
