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


def pak_names() -> dict[str, str]:
    """CharacterID -> English display name, straight from `DT_PalNameText_Common`.

    The lexicon does not cover everything the exception table names - Necromus reaches
    the sheet as `BlackCentaur` without this. But the table also ships **localisation
    placeholders**: `BeardedDragon` has SourceString `en_text`, which is the string the
    game shows when a name was never written. Taking it at face value would print
    "en_text" as a Pal name, which is precisely the well-formed-and-wrong shape this
    project keeps paying for, so placeholders are rejected rather than passed through.
    """
    raw = json.loads((REPO / "data" / "raw" / "pal_names_en.json").read_text(
        encoding="utf-8"))
    rows = (raw[0] if isinstance(raw, list) else raw)["Rows"]
    out = {}
    for key, val in rows.items():
        if not key.startswith("PAL_NAME_"):
            continue
        text = (val.get("TextData") or {}).get("LocalizedString") or ""
        # `en_text` is the placeholder; an empty string is the same thing said quietly.
        if not text or text == "en_text":
            continue
        out[key[len("PAL_NAME_"):]] = text
    return out


def pak_paldeck_numbers() -> dict[str, str]:
    """CharacterID -> Paldeck number, for Pals `breeding.json` does not carry.

    The same second tier `pak_names` provides for names, and it exists for the same
    reason. Not every Pal on this sheet is a breeding *tribe*: Shadowbeak reaches it from
    the exception table as `BlackGriffon`, which has no tribe row and so no
    `zukan_index` - but the pak knows it is `#189`. Resolving names from two sources and
    numbers from one produced exactly one row reading `| Shadowbeak | Shadowbeak |`
    between eight numbered ones, which looks like missing data about that Pal rather than
    a difference in where the row came from.
    """
    raw = json.loads((REPO / "data" / "raw" / "pal_monster_parameter.json").read_text(
        encoding="utf-8"))
    out = {}
    for cid, row in (raw.get("Rows") or {}).items():
        if not isinstance(row, dict):
            continue
        out[cid] = paldeck_number({"zukan_index": row.get("ZukanIndex"),
                                   "zukan_suffix": row.get("ZukanIndexSuffix")})
    return {k: v for k, v in out.items() if v}


def paldeck_number(tribe: dict) -> str:
    """The Paldeck number as the game prints it, e.g. `#171B`, or `""`.

    **A lookup key for a human standing in front of the game**, which is the only reason
    it is here: the sheet asks a tester to find specific Pals, and it is unusually full of
    variants - Ignis, Noct, Cryst, Terra, Primo - whose names differ by one word.

    `zukan_suffix` is what makes this worth printing rather than dangerous. A variant does
    **not** get its own number; it gets the base Pal's number plus a letter, so Eidrolon
    is `#171` and Eidrolon Ignis is `#171B`. The index alone would label both `#171` and
    quietly send the tester to the wrong Pal - and a breeding row is exactly where that
    costs an egg and produces a confidently wrong result.

    Empty rather than invented when the index is missing or -1 (anything outside the
    Paldeck). A row whose name resolved stays on the sheet without a number.
    """
    idx = tribe.get("zukan_index")
    if idx is None or idx < 0:
        return ""
    return f"#{idx:03d}{(tribe.get('zukan_suffix') or '').strip()}"


def catch_levels(version: str) -> dict[str, int]:
    """Display name -> the lowest level this Pal spawns at in the wild.

    **The number that decides whether a tester can run a row at all**, and its absence is
    why the sheet's own priority guidance was wrong: Block 1 opens with three Pals that
    spawn only at level 80, under a heading that says to do it first and stop if it fails.

    Alpha areas are excluded. An alpha is a single fixed high-level encounter, not how a
    breeding parent is obtained, and including it would raise the floor for Pals that are
    ordinarily catchable much earlier.

    A Pal with no ordinary spawn at all is absent rather than zero - Celesdir Noct and
    Moldron Cryst are breed-only, which is a real thing to know about a row and not a
    missing measurement.
    """
    spawns = json.loads(
        (REPO / "data" / version / "pal_spawns.json").read_text(encoding="utf-8"))
    low: dict[str, int] = {}
    for area in spawns["areas"]:
        if area.get("kind") == "alpha":
            continue
        lv = area.get("level_min")
        if lv is None:
            continue
        pal = area["pal"]
        low[pal] = min(low.get(pal, lv), lv)
    return low


def build(version: str) -> str:
    data = json.loads(
        (REPO / "data" / version / "breeding.json").read_text(encoding="utf-8"))
    tribes = data["tribes"]

    # Display names, in order of authority: the project's own canonical name, then the
    # game's English table, then nothing. Nothing means the row is dropped - a tester
    # cannot search the Paldeck for `YakushimaBoss001`, so a row naming it is not a
    # test, it is a puzzle.
    from_pak = pak_names()
    name: dict[str, str] = {}
    for t in tribes:
        resolved = t.get("name") or from_pak.get(t["character_id"])
        if resolved:
            name[t["tribe"]] = resolved
    for cid, display in from_pak.items():
        name.setdefault(cid, display)
    # Paldeck numbers, keyed the same way names are, so `label()` can look either up with
    # one id. Read from `breeding.json` rather than the raw table: it already carries the
    # index and suffix for all 260 tribes, and taking it from the ingested dataset keeps
    # the sheet consistent with what the model was computed from.
    number = {t["tribe"]: paldeck_number(t) for t in tribes}
    for cid, num in pak_paldeck_numbers().items():
        if not number.get(cid):
            number[cid] = num

    # Lowest wild spawn level, keyed by display name (which is how pal_spawns names Pals).
    level = catch_levels(version)

    def label(cid: str) -> str:
        """`#171B Eidrolon Ignis (lv 75)` - the three things a tester needs to find one.

        Number first because it is what the Paldeck is scanned by; level last because it
        is what decides whether the row is attemptable at all.

        Every part degrades independently: a missing number must not remove a name that
        did resolve, and a missing level means *no ordinary wild spawn* - `(bred only)`,
        which is a fact about the Pal rather than a gap in the data.
        """
        shown = name.get(cid, cid)
        num = number.get(cid)
        head = f"{num} {shown}" if num else shown
        lv = level.get(shown)
        if lv is not None:
            return f"{head} (lv {lv})"
        # Only claim "bred only" for Pals we actually resolved a name for; an unresolved
        # id says nothing about spawns.
        return f"{head} (bred only)" if shown in name.values() else head

    rank = {t["tribe"]: t["rank"] for t in tribes}
    # Eligible children: the derived rule under test - no Zukan suffix. Check B in the
    # scorer says this rule is imperfect, so the sheet tests it rather than trusting it.
    eligible = sorted((t["rank"], t["tribe"]) for t in tribes if not t.get("zukan_suffix"))

    exceptions = data["exceptions"]
    exc_pairs = {(e["parent_a"], e["parent_b"]) for e in exceptions}
    exc_pairs |= {(b, a) for a, b in exc_pairs}

    # Parents have to be CAUGHT before they can be bred, so a row naming a Pal the
    # tester cannot readily obtain is not a test. `kind == "normal"` rather than any
    # spawn at all: Necromus is in the overworld, but only as a field alpha, and
    # "catch two Necromus" is not a task anyone can run.
    spawns = json.loads(
        (REPO / "data" / version / "pal_spawns.json").read_text(encoding="utf-8"))
    catchable = {a["pal"] for a in spawns["areas"] if a["kind"] == "normal"}

    def named(*ids: str) -> bool:
        """Every id on the row resolves to a name a tester can find in game."""
        return all(name.get(i) for i in ids)

    def obtainable(*ids: str) -> bool:
        """Every id names a Pal with an ordinary overworld spawn."""
        return all(name.get(i) in catchable for i in ids)

    def usable(a: str, b: str) -> bool:
        return (a in rank and b in rank and (a, b) not in exc_pairs
                and named(a, b) and obtainable(a, b))

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
            if f == h and f not in (a, b) and named(f):
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
            predicted = child_of(rank[a], rank[b], eligible, "floor")
            if (nearest_any not in elig_set and nearest_any not in seen_skipped
                    and named(nearest_any, predicted)):
                seen_skipped.add(nearest_any)
                skip_rows.append((a, b, nearest_any, predicted))

    # --- Block 4: exception rows, sampled across kinds.
    def row_named(e: dict) -> bool:
        # The child need only be nameable; the PARENTS must also be catchable, which
        # is what removes the legendary self-pairs. "Breed two Necromus" is a correct
        # row and an impossible errand.
        return (named(e["parent_a"], e["parent_b"], e["child_character_id"])
                and obtainable(e["parent_a"], e["parent_b"]))

    # Rows naming something with no English name are counted, then dropped. Most are
    # Yakushima and raid content whose names the table ships as placeholders.
    unnamed_rows = sum(1 for e in exceptions if not row_named(e))
    self_pairs = [e for e in exceptions
                  if e["parent_a"] == e["parent_b"] and row_named(e)]
    cross_pairs = [e for e in exceptions
                   if e["parent_a"] != e["parent_b"] and row_named(e)]
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
        return f"| {label(a)} | {label(b)} | **{label(child)}** |  |"

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
    add("**3. The tester has to be able to catch the parents.** Added 2026-08-14, after "
        "the first tester with breeding unlocked reported catching nothing above ~60. "
        "Every Pal below carries its Paldeck number and its **lowest wild spawn level** — "
        "`#171B Eidrolon Ignis (lv 75)` — so this is now checkable per row instead of "
        "discovered halfway down the sheet. The letter matters: a variant shares the base "
        "Pal's number, so `#171` Eidrolon and `#171B` Eidrolon Ignis are different Pals in "
        "one Paldeck slot. `(bred only)` marks a Pal with no wild spawn at all. Alpha "
        "encounters are excluded from the level, since an alpha is not how you obtain a "
        "breeding parent.\n")
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
            add(f"| {label(a)} | {label(b)} | {label(f)} | "
                f"{label(h)} |  |")
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
        add(f"| {label(a)} | {label(b)} | _{label(skipped)}_ | "
            f"**{label(pred)}** |  |")
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
    if unnamed_rows:
        add(f"_{unnamed_rows} of the {len(exceptions)} exception rows are omitted, on two "
            "grounds. Some name a Pal with no English name at all — "
            "`DT_PalNameText_Common` ships the `en_text` placeholder for a few entries, "
            "`BeardedDragon` among them. The rest name a parent with no ordinary "
            "overworld spawn, which takes out the legendary self-pairs: "
            "*Necromus + Necromus* is a correct row and an impossible errand, and "
            "*Mau + Pengullet* goes with them because Mau is dungeon-only. So the claim "
            "'legendaries breed true' is **left untested** — it was never testable, and "
            "saying so beats printing a row nobody can run._\n")
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
    add("Priority **if you can catch the parents**: Block 1, then Block 2. Block 1 decides "
        "whether the model works at all and Block 2 settles a question nothing else can.\n")
    add("**If your highest catch is around level 60, start with Block 4 instead.** That is "
        "not a preference, it is what the levels above say: 14 of Block 1's 19 Pals spawn "
        "only above level 60 and three of them (Eidrolon, Renjishi, Ophydia) are level 80, "
        "while Block 4 runs from level 3 and has just 4 Pals above 60. **Block 1 asks an "
        "endgame roster for the block it calls the cheapest.** A refuted exception row is "
        "worth less than a refuted baseline row, and it is worth infinitely more than a "
        "baseline row nobody can attempt.\n")
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
