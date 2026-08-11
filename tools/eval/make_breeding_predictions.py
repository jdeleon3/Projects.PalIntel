"""Generate Docs/breeding-verification.md - a falsification sheet, not a data-collection form.

The ADR-0008 gate needs an independent check and no community source can provide one:
calculators derive from CombiRank exactly as we do, and the wiki's rank column disagrees
with the pak at Spearman 0.80 over 166 Pals (tools/eval/score_breeding.py). Only the game
can settle it.

So this emits **predictions**, each chosen because a specific outcome refutes a specific
claim. A tester confirms or refutes; nobody has to know what any row is testing. Every
block states what its failure would mean, because a sheet whose rows all pass for
uninteresting reasons is a sheet that measured nothing.

The predictions are the model's claims, not facts. If a row is wrong, the model is wrong -
that is the point, and rows are deliberately chosen where being wrong is most likely.

Usage: python tools/eval/make_breeding_predictions.py --version 1.0.2
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEST = REPO / "Docs" / "breeding-verification.md"

# The build the predictions were computed from. A tester on a different build is
# measuring a different game, and this is the one precondition that invalidates
# everything else on the sheet.
BUILD_ID = "24467282"
PAK_DATE = "2026-07-30"


def child_of(ra: int, rb: int, candidates: list[tuple[int, str]], rounding: str) -> str:
    avg = (ra + rb) // 2 if rounding == "floor" else (ra + rb + 1) // 2
    return min(candidates, key=lambda rt: (abs(rt[0] - avg), rt[0]))[1]


def build(version: str) -> str:
    data = json.loads(
        (REPO / "data" / version / "breeding.json").read_text(encoding="utf-8"))
    tribes = data["tribes"]
    name = {t["tribe"]: (t.get("name") or t["character_id"]) for t in tribes}
    rank = {t["tribe"]: t["rank"] for t in tribes}
    # Eligible children: the derived rule under test - no Zukan suffix. Check B in the
    # scorer says this rule is imperfect, so the sheet tests it rather than trusting it.
    eligible = sorted((t["rank"], t["tribe"]) for t in tribes if not t.get("zukan_suffix"))

    exceptions = data["exceptions"]
    exc_pairs = {(e["parent_a"], e["parent_b"]) for e in exceptions}
    exc_pairs |= {(b, a) for a, b in exc_pairs}

    def usable(a: str, b: str) -> bool:
        return (a in rank and b in rank and (a, b) not in exc_pairs
                and name.get(a) and name.get(b))

    # --- Block 1: agreement pairs. Both conventions agree, no exception applies.
    # If these fail, the model is wrong at the root and nothing after matters.
    #
    # At most one row per first parent, and the second parent strided across the rank
    # order. The first cut of this took the first N pairs it found and produced twelve
    # rows that all began with the same Pal - twelve tests of one corner of the table,
    # which is close to no test at all.
    baseline = []
    keys = [t["tribe"] for t in tribes]
    for i, a in enumerate(keys):
        for b in keys[i + 1::7]:
            if not usable(a, b):
                continue
            f = child_of(rank[a], rank[b], eligible, "floor")
            h = child_of(rank[a], rank[b], eligible, "floor_plus_half")
            if f == h and f not in (a, b):
                baseline.append((a, b, f))
                break  # one row per first parent, so the sheet spans the rank range

    # --- Block 2: rounding discriminators. The two conventions predict DIFFERENT
    # children, so one result eliminates one convention. These are the highest-value
    # rows on the sheet and there are not many of them.
    discriminators = []
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if not usable(a, b):
                continue
            f = child_of(rank[a], rank[b], eligible, "floor")
            h = child_of(rank[a], rank[b], eligible, "floor_plus_half")
            if f != h:
                discriminators.append((a, b, f, h))

    # --- Block 3: eligibility. Pairs whose average is NEAREST an ineligible variant.
    # The model says the child skips it for the next eligible rank. If the variant
    # hatches, the eligibility rule is wrong and check B's disagreement was real.
    all_ranked = sorted((t["rank"], t["tribe"]) for t in tribes)
    elig_set = {t for _, t in eligible}
    # One row per SKIPPED Pal, not per pair: the claim under test is about the skipped
    # variant, so ten rows skipping the same one are a single test written ten times.
    skip_rows, seen_skipped = [], set()
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if not usable(a, b):
                continue
            avg = (rank[a] + rank[b]) // 2
            nearest_any = min(all_ranked, key=lambda rt: (abs(rt[0] - avg), rt[0]))[1]
            if nearest_any not in elig_set and nearest_any not in seen_skipped:
                seen_skipped.add(nearest_any)
                skip_rows.append((a, b, nearest_any,
                                  child_of(rank[a], rank[b], eligible, "floor")))

    # --- Block 4: exception rows, sampled across kinds.
    self_pairs = [e for e in exceptions if e["parent_a"] == e["parent_b"]]
    cross_pairs = [e for e in exceptions if e["parent_a"] != e["parent_b"]]
    # An exception whose child the rank rule would ALSO produce proves nothing, so
    # prefer rows where the rule disagrees - those are the table earning its place.
    overriding = []
    for e in cross_pairs:
        ra, rb = rank.get(e["parent_a"]), rank.get(e["parent_b"])
        if ra is None or rb is None:
            continue
        if child_of(ra, rb, eligible, "floor") != e["child_character_id"]:
            overriding.append(e)

    def row(a: str, b: str, child: str) -> str:
        return f"| {name.get(a, a)} | {name.get(b, b)} | **{name.get(child, child)}** |  |"

    L: list[str] = []
    add = L.append
    add("# Breeding verification\n")
    add("*Generated by `tools/eval/make_breeding_predictions.py`. Regenerate rather than "
        "edit — the predictions are computed from `data/<version>/breeding.json`.*\n")
    add("This sheet exists because **no community source can validate this model.** Every "
        "breeding calculator derives the child from the same per-Pal rank we do, so "
        "agreement would only show that two implementations of one formula match. And the "
        "wiki's published rank column disagrees with the pak at Spearman 0.80 across 166 "
        "Pals — strongly correlated, differently ordered, so not a units difference. "
        "[ADR-0008](adr/0008-breeding-graph-derivation.md) is Provisional until this "
        "sheet runs.\n")
    add("**Everything below is a claim the model makes, not a fact.** Rows were chosen "
        "because being wrong is plausible. A refuted row is the sheet working.\n")
    add("---\n")
    add("## Before anything: the version must match\n")
    add(f"These predictions were computed from a pak dated **{PAK_DATE}**, Steam buildid "
        f"**`{BUILD_ID}`**.\n")
    add("Breeding ranks are rebalanced between patches — that is the most likely reason "
        "the wiki disagrees with us. **A tester on a different build is measuring a "
        "different game, and every row below becomes meaningless.** This is the one "
        "precondition that invalidates the whole sheet, so check it first.\n")
    add("Steam → right-click Palworld → Properties → Updates → the build id is listed "
        "there; or read `buildid` in `steamapps/appmanifest_1623730.acf`. Turn off "
        "automatic updates for the duration, or a patch mid-session silently splits the "
        "results into two datasets.\n")
    add("Nothing else is needed: no save file, no bot, no Discord. Breeding mechanics are "
        "global, so any player on the right build can run this — the results are about "
        "the game, not about a save.\n")
    add("---\n")
    add("## Block 1 — baseline (the model is right at all)\n")
    add("Both rounding conventions agree here and no exception applies, so these are the "
        "model's least adventurous claims. **If these fail, stop** — nothing later on the "
        "sheet is worth the eggs, and the finding is that the rank model does not hold.\n")
    add("| Parent A | Parent B | Predicted child | Actual |")
    add("|---|---|---|---|")
    for a, b, c in baseline[:12]:
        add(row(a, b, c))
    add("")
    add("## Block 2 — the rounding convention (highest value rows here)\n")
    add("ADR-0008 never stated whether the average rounds down or half-up, and it only "
        "matters when the average falls exactly between two ranks. Each row below is a "
        "pair where **the two conventions predict different children**, so a single "
        "result eliminates one of them.\n")
    if discriminators:
        add(f"There are **{len(discriminators)}** such pairs in the whole dataset, which "
            "is why they are worth doing first after the baseline.\n")
        add("| Parent A | Parent B | If round-down | If round-half-up | Actual |")
        add("|---|---|---|---|---|")
        for a, b, f, h in discriminators[:12]:
            add(f"| {name.get(a, a)} | {name.get(b, b)} | {name.get(f, f)} | "
                f"{name.get(h, h)} |  |")
    else:
        add("**No pair can separate the two conventions, and the reason is arithmetic "
            "rather than luck.** All 260 ranks are multiples of 10, so every sum is even, "
            "so `(a+b)//2` and `(a+b+1)//2` are always the same number. The two "
            "conventions are not merely indistinguishable in this dataset — they are the "
            "same function on it.\n")
        add("So ADR-0008's unstated rounding convention was never a real ambiguity, and "
            "no eggs need to be spent on it. It becomes one again only if a patch ever "
            "ships a rank that is not a multiple of 10, which `build_breeding.py` would "
            "have to start checking for.\n")
    add("")
    add("## Block 3 — eligibility (the derived rule most likely to be wrong)\n")
    add("The pak has **no** 'can be a child' field. The rule under test is *a Pal with a "
        "Paldeck suffix (`005B`) can be a parent but is never produced by averaging* — "
        "derived by us, and the scorer already found it disagreeing with the wiki on 13 "
        "Pals. It may be the version gap; it may be a bad rule.\n")
    add("In each row the average lands nearest the **skipped** Pal. The model says the "
        "egg is the predicted one instead. **If the skipped Pal hatches, the rule is "
        "wrong** — and that would change which Pals Q3 can ever offer as a target.\n")
    add("| Parent A | Parent B | Nearest by rank (skipped) | Predicted child | Actual |")
    add("|---|---|---|---|---|")
    for a, b, skipped, pred in skip_rows[:10]:
        add(f"| {name.get(a, a)} | {name.get(b, b)} | _{name.get(skipped, skipped)}_ | "
            f"**{name.get(pred, pred)}** |  |")
    add("")
    add("## Block 4 — the exception table\n")
    add(f"{len(exceptions)} rows override the rank rule, and **{len(overriding)}** of them "
        "produce a child the rule would not. Those are the ones worth testing; a row the "
        "rule already agrees with proves nothing either way.\n")
    add("| Parent A | Parent B | Predicted child | Actual |")
    add("|---|---|---|---|")
    for e in overriding[:10]:
        add(row(e["parent_a"], e["parent_b"], e["child_character_id"]))
    add("")
    add("### Self-pairs — two claims in one\n")
    add(f"{len(self_pairs)} of the {len(exceptions)} rows pair a Pal with itself, and they "
        "encode two different things. Legendaries breed true because nothing else can "
        "make them; variants breed true so a line can be kept once you have one. Both "
        "should hold, and a failure in either is interesting.\n")
    add("| Parent A | Parent B | Predicted child | Actual |")
    add("|---|---|---|---|")
    for e in self_pairs[:8]:
        add(row(e["parent_a"], e["parent_b"], e["child_character_id"]))
    add("")
    add("---\n")
    add("## Recording results\n")
    add("Fill the **Actual** column with what hatched. A wrong prediction is worth more "
        "than a right one and should be recorded verbatim rather than corrected — if a "
        "pattern is going to show up, it will show up in the misses.\n")
    add("Priority if there is not time for all of it: **Block 1, then Block 2.** Block 1 "
        "decides whether the model works at all and Block 2 settles a question nothing "
        "else can. Blocks 3 and 4 refine it.\n")
    add(f"Then run `python tools/eval/score_breeding.py --version {version}` with the "
        "results to close the ADR-0008 gate, and move the ADR from Provisional to "
        "Accepted — or to the `TableBasedBreedingModel` fallback it already names.\n")
    return "\n".join(L) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="1.0.2")
    args = ap.parse_args()
    DEST.write_text(build(args.version), encoding="utf-8")
    print(f"breeding verification sheet -> {DEST}")


if __name__ == "__main__":
    main()
