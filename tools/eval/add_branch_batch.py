"""Add a recording batch for the branches no transcript covers.

`counters` and `item_source` are both shipped-or-shipping with the same hole in their
evidence: **every prompt in the 240-transcript set predates them.** Scoring the counters
fast path across 480 cases claimed nothing and changed nothing, which bounds the
regression at zero and says nothing at all about whether the branch works. `item_source`
has the same gap, recorded in STATUS since the class landed.

This batch closes it from the input side, so the next recording session captures the
utterances that would exercise them.

**Numbered -1 so it records first.** `record_stt.py` takes `sorted(unfinished)[0]` when
no batch is given, so -1 is picked ahead of the remaining full batches without
renumbering anything already recorded. Ids are prefixed `B` for the same reason - they
cannot collide with the existing `P##` series, and a half-recorded set stays readable.

**What the cases are chosen to catch**, rather than to pass:

* *Tier-ambiguous phrasings.* The counters branch claims only when a counter cue is
  present and a location cue is not. Utterances carrying both are the abstention path,
  and nothing currently proves that path is reachable in real speech rather than only in
  a unit test.
* *Items that are ordinary English words.* Wool, bone, leather and flame organ are in the
  tool enum and deliberately not in the lexicon (ADR-0016), so nothing ranks them and the
  router resolves them on sentence context alone. That is the risk the design took.
* *Names speech actually mangles.* The 2026-08-11 session produced `Vanworm`, `man worm`,
  `Makora`, `Pantlion` and `Disneyland Ball Drop`. Those Pals are included by name so the
  recording captures the same failures under measurement instead of anecdotally.
* *The near-miss band.* Slightly-wrong tokens are more dangerous than badly-wrong ones,
  because the fast path claims the first and defers the second.

Usage: python tools/eval/add_branch_batch.py            # write it
       python tools/eval/add_branch_batch.py --dry-run  # print it
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROMPTS = REPO / "data" / "stt_eval" / "prompts.json"
BATCH = -1

# (text, expected lexicon entities, expected branch, difficulty)
# Entities are only listed where the LEXICON can rank them. Items are deliberately not
# in it, so item prompts carry [] and name the item in `expect_item` instead - claiming
# an entity the corrector cannot produce would score the branch against an impossible
# bar.
COUNTER = [
    ("hey pal how do I beat Anubis", ["Anubis"], "counter"),
    ("hey pal what counters Bellanoir", ["Bellanoir"], "counter"),
    ("hey pal what should I bring to fight Grizzbolt", ["Grizzbolt"], "counter"),
    ("hey pal what's good against Jetragon", ["Jetragon"], "counter"),
    ("hey pal how do I kill Frostallion", ["Frostallion"], "counter"),
    ("hey pal what's Necromus weak to", ["Necromus"], "counter"),
    ("hey pal what should I use against Paladius", ["Paladius"], "counter"),
    ("hey pal how do I beat the Chillet alpha", ["Chillet"], "counter"),
    ("hey pal what beats Vanwyrm", ["Vanwyrm"], "counter"),
    ("hey pal how do I take on Blazamut", ["Blazamut"], "counter"),
    ("hey pal what's strong against Lyleen", ["Lyleen"], "counter"),
    ("hey pal how do I defeat Orserk", ["Orserk"], "counter"),
]

# Both cue families in one sentence. The fast path must NOT claim these; they are the
# only evidence that the abstention is reachable in speech.
AMBIGUOUS = [
    ("hey pal where can I find something to beat Anubis", ["Anubis"], "ambiguous"),
    ("hey pal where do I go to fight Bellanoir", ["Bellanoir"], "ambiguous"),
    ("hey pal find me a counter for Jetragon", ["Jetragon"], "ambiguous"),
    ("hey pal where's the nearest Pal that beats Frostallion",
     ["Frostallion"], "ambiguous"),
]

# Ordinary English words that are items. Nothing ranks them.
ITEM = [
    ("hey pal who drops flame organs", "Flame Organ"),
    ("hey pal what drops wool", "Wool"),
    ("hey pal where do I get leather", "Leather"),
    ("hey pal who drops bone", "Bone"),
    ("hey pal what drops paldium fragment", "Paldium Fragment"),
    ("hey pal who drops ancient civilization parts", "Ancient Civilization Parts"),
    ("hey pal where do I get high quality pal oil", "High Quality Pal Oil"),
    ("hey pal what drops ice organ", "Ice Organ"),
    ("hey pal who drops venom gland", "Venom Gland"),
    ("hey pal what drops gold coin", "Gold Coin"),
]

# Pals speech has already been measured mangling, in the phrasings that mangled them.
MANGLED = [
    ("hey pal what does Vanwyrm drop", ["Vanwyrm"], "drops"),
    ("hey pal what do I get from Astralym and Mycora",
     ["Astralym", "Mycora"], "drops"),
    ("hey pal what does Lamball drop", ["Lamball"], "drops"),
    ("hey pal how do I beat Mycora", ["Mycora"], "counter"),
    ("hey pal what drops paldium fragments", "Paldium Fragment"),
]


def build() -> list[dict]:
    rows: list[dict] = []

    # Which path is expected to answer. The fast path cannot claim item_source at all -
    # items are deliberately out of the lexicon - and several counter phrasings put the
    # named Pal in the attacker position, so the model keeps those too. Scoring them as
    # fast-path misses measures the wrong thing: they are working as designed.
    MODEL_ONLY_TEXT = ("good against", "use against", "strong against")

    def add(text, entities, branch, difficulty, item=None):
        path = "model" if (branch == "item_source"
                           or any(t in text.lower() for t in MODEL_ONLY_TEXT)
                           or len(entities) > 1) else "fast"
        rows.append({
            "expect_path": path,
            "group": "utterance",
            "text": text,
            "expect_entities": list(entities),
            "expect_item": item,
            "expect_branch": branch,
            "difficulty": difficulty,
            "id": f"B{len(rows) + 1:02d}",
            "batch": BATCH,
        })

    for text, ents, branch in COUNTER:
        add(text, ents, branch, "counter")
    for text, ents, branch in AMBIGUOUS:
        add(text, ents, branch, "tier_ambiguous")
    for text, item in ITEM:
        add(text, [], "item_source", "item_word")
    for row in MANGLED:
        if len(row) == 3:
            text, ents, branch = row
            add(text, ents, branch, "known_mangled")
        else:
            text, item = row
            add(text, [], "item_source", "known_mangled", item=item)
    # Fill the item rows' `expect_item` from ITEM, which `add` could not see.
    for row, (_, item) in zip([r for r in rows if r["difficulty"] == "item_word"], ITEM):
        row["expect_item"] = item
    return rows


def validate(rows: list[dict]) -> list[str]:
    """A prompt naming something the pipeline cannot produce is not a test case.

    Two ways that happens here, both silent: a Pal the lexicon does not carry, so the
    corrector can never return it and the prompt scores as a permanent miss; and a
    counter target with no boss form, which the branch is *supposed* to refuse, so
    recording it would measure the refusal rather than the branch.
    """
    errs = []
    lexicon = json.loads(
        (REPO / "data" / "1.0.2" / "lexicon.json").read_text(encoding="utf-8"))
    names = {p["canonical"].lower() for p in lexicon["pals"]}
    bosses_path = REPO / "data" / "1.0.2" / "bosses.json"
    bosses = set()
    if bosses_path.exists():
        bosses = {b["name"].lower() for b in
                  json.loads(bosses_path.read_text(encoding="utf-8"))["entries"]
                  if b.get("name")}

    for r in rows:
        for e in r["expect_entities"]:
            if e.lower() not in names:
                errs.append(f"{r['id']}: {e!r} is not in the lexicon")
            if r["expect_branch"] == "counter" and bosses and e.lower() not in bosses:
                errs.append(f"{r['id']}: {e!r} has no boss form, so the counter branch "
                            f"is required to refuse it")
    return errs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data = json.loads(PROMPTS.read_text(encoding="utf-8"))
    rows = build()

    errs = validate(rows)
    if errs:
        # Fail closed rather than record a batch that cannot be scored.
        print("prompt batch FAILED validation:")
        for e in errs:
            print(f"  {e}")
        raise SystemExit(1)

    if args.dry_run:
        for r in rows:
            extra = r["expect_item"] or ", ".join(r["expect_entities"]) or "-"
            print(f"  {r['id']}  {r['difficulty']:<15} {extra:<28} {r['text']}")
        print(f"\n{len(rows)} prompts, batch {BATCH}")
        return

    # Idempotent: re-running replaces the batch rather than appending a second copy.
    kept = [p for p in data["prompts"] if p.get("batch") != BATCH]
    data["prompts"] = rows + kept
    data["count"] = len(data["prompts"])
    data["branch_batch_note"] = (
        "Batch -1 covers counters and item_source, which no P## prompt does: the "
        "original set predates both classes. Numbered -1 so record_stt.py takes it "
        "first without renumbering anything already recorded.")
    PROMPTS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"batch {BATCH}: {len(rows)} prompts -> {PROMPTS}")
    from collections import Counter
    for d, n in Counter(r["difficulty"] for r in rows).most_common():
        print(f"  {d:<16} {n}")
    print(f"\n  record with:  python tools/eval/record_stt.py --condition quiet")
    print(f"  it is next automatically - record_stt takes sorted(unfinished)[0]")


if __name__ == "__main__":
    main()
