"""Extend the STT evaluation prompt set (assumption A5).

v3 - powered to resolve differences the v2 set cannot.

v2's 36 utterances measure a router to +/-3.6 points (see Docs/04-roadmap.md, "Variance
sized before tuning"). That is wider than every difference now on the table: Gemini 3.6
Flash leads Opus 5 by 5.6 points on one run, which is two utterances, and the three
no-entity prompts make the false-positive rate a 1-in-3 coin. The set is out of
resolution, so more runs cannot help - only more prompts can.

**This script appends. It never regenerates.** The existing prompts are copied through
byte-identical and new ones are given fresh ids, because record_stt.py skips ids already
in a condition's manifest: re-recording is therefore unnecessary, and the 40 takes
already on disk stay valid. Regenerating instead would silently invalidate them the
moment the lexicon or the sampling changed.

What v3 adds, and why each one is a measured gap rather than a guess:

  no-entity     3 -> 15   The false-positive test. Qwen3 invented a Pal on 1 of 3 and
                          every other model scored 0 of 3; neither result means anything
                          at n=3. This is the safety bar, so it gets the most power.
  variant Pals  0 -> 12   v2 excluded multi-word names outright (`" " not in canonical`).
                          Variants are exactly where models hedge: Qwen3 answered
                          "where can I find Kitsun?" with Kitsun *and* Kitsun Noct. The
                          set currently cannot see that failure at all.
  frame-word    0 -> 10   Deliberate collisions with phrasing the corrector mis-matches.
                          "show me" scores 0.71 against Shroomer and "against the" 0.59
                          against Maraith; those spurious candidates are what Qwen3
                          selected. Only accidental coverage in v2.
  two-entity    2 -> 10   Arity handling, and the over-naming failure mode.
  resource     4 -> 12    Includes crude_oil, which v2 omitted entirely.

Output: data/stt_eval/prompts.json (v3). Then:
    python tools/eval/record_stt.py --condition quiet     # records only the new ids
    python tools/eval/score_stt.py  --condition quiet
    python tools/eval/score_router.py --condition quiet --model <model>
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEXICON = REPO / "data" / "1.0.2" / "lexicon.json"
OUT = REPO / "data" / "stt_eval"

SEED = 20260809  # distinct from v2's 20260808; fixed so v3 is reproducible

# Phrasings whose frame words the corrector mis-matches against real Pal names. These
# are the ones that produced wrong entities, not just misses - see the Qwen3 table in
# Docs/04-roadmap.md.
FRAME_WORD_TEMPLATES = [
    "hey pal show me {pal} near my base",
    "hey pal is {pal} any good against the first tower",
    "hey pal can you find me a {pal} somewhere close",
    "hey pal what's the best way to catch {pal}",
    "hey pal do I need a better sphere for {pal}",
]

PAL_TEMPLATES = [
    "hey pal where can I find {pal}",
    "hey pal where do {pal} spawn",
    "hey pal how do I breed {pal}",
    "hey pal what's the breeding combo for {pal}",
    "hey pal is {pal} good for mining",
    "hey pal what element is {pal}",
    "hey pal should I use {pal} against the first tower",
    "hey pal where's the nearest {pal}",
    "hey pal what work suitability does {pal} have",
    "hey pal is {pal} worth levelling up",
]

# Variant names ("X Noct", "X Aqua"). The base form is a live distractor at high score,
# which is the condition that produced over-naming.
VARIANT_TEMPLATES = [
    "hey pal where can I find {pal}",
    "hey pal what element is {pal}",
    "hey pal how do I breed {pal}",
    "hey pal is {pal} better than the normal one",
]

TWO_PAL_TEMPLATES = [
    "hey pal can I breed {pal} with {pal2}",
    "hey pal is {pal} better than {pal2} for handiwork",
    "hey pal which should I use {pal} or {pal2}",
    "hey pal do {pal} and {pal2} make anything good",
    "hey pal compare {pal} and {pal2} for combat",
]

RESOURCE_TEMPLATES = [
    "hey pal where's the nearest {res}",
    "hey pal find me a {res} spot for level twenty",
    "hey pal where's the closest {res} deposit",
    "hey pal show me {res} near my base",
    "hey pal is there any {res} around here",
    "hey pal I need {res} for a new base",
]

# The false-positive test. Deliberately varied: some name game concepts that sound like
# they could be entities (traits, towers, techs), because an entity-shaped noun is what
# provokes a hallucinated match.
NO_ENTITY = [
    "hey pal what should I research next",
    "hey pal where should I put my second base",
    "hey pal what does the artisan trait do",
    "hey pal how do I raise my capture rate",
    "hey pal what's the fastest way to level up",
    "hey pal should I upgrade my base or my gear first",
    "hey pal how many workers can I have at one base",
    "hey pal what does the lucky trait actually give me",
    "hey pal is it worth building a second breeding pen",
    "hey pal how do I get more technology points",
    "hey pal what should I do after the first tower",
    "hey pal how do I stop my pals getting depressed",
    "hey pal what's the best weapon at this level",
    "hey pal do I need to feed my pals overnight",
    "hey pal how do I unlock the next tier of gear",
]

RESOURCES = ["coal", "ore", "sulfur", "quartz", "crude oil"]


def unusual(name: str) -> int:
    """v2's difficulty proxy, kept identical so the bands mean the same thing."""
    w = re.sub(r"[^a-z]", "", name.lower())
    rare = sum(1 for a, b in zip(w, w[1:]) if a + b in {
        "fm", "mn", "rm", "tz", "zz", "kt", "gp", "wy", "jo", "xt", "vy", "nk", "th"})
    return len(w) + rare * 3


def natural(text: str) -> str:
    """'a ore spot' reads wrong aloud, and a prompt that trips the reader measures the
    reader rather than the model."""
    return re.sub(r"\ba (?=[aeiou])", "an ", text)


def main() -> None:
    rng = random.Random(SEED)
    lex = json.loads(LEXICON.read_text(encoding="utf-8"))
    existing = json.loads((OUT / "prompts.json").read_text(encoding="utf-8"))
    keep = existing["prompts"]
    used = {e for p in keep for e in p["expect_entities"]}

    simple = [p["canonical"] for p in lex["pals"]
              if p["in_paldeck"] and " " not in p["canonical"]
              and p["canonical"] not in used]
    variants = [p["canonical"] for p in lex["pals"]
                if p["in_paldeck"] and " " in p["canonical"]]

    ranked = sorted(simple, key=unusual)
    third = len(ranked) // 3
    bands = {"easy": ranked[:third], "medium": ranked[third:2 * third],
             "hard": ranked[2 * third:]}
    for b in bands.values():
        rng.shuffle(b)
    rng.shuffle(variants)

    new: list[dict] = []

    seen_text = {p["text"] for p in keep}

    def add(group: str, text: str, ents: list[str], difficulty: str) -> None:
        """Skip anything already recorded. Three of the no-entity lines below are
        verbatim v2 prompts; re-recording them would buy a second take of an existing
        item rather than a new one, and quietly double its weight in the total."""
        t = natural(text)
        if t in seen_text:
            return
        seen_text.add(t)
        new.append({"group": group, "text": t,
                    "expect_entities": ents, "difficulty": difficulty})

    # Single Pals, weighted toward hard, cycling every template so phrasing and entity
    # are not correlated the way v2's fixed cycle made them.
    picks = ([("hard", n) for n in bands["hard"][:20]]
             + [("medium", n) for n in bands["medium"][:14]]
             + [("easy", n) for n in bands["easy"][:8]])
    rng.shuffle(picks)
    for i, (band, pal) in enumerate(picks):
        add("utterance", PAL_TEMPLATES[i % len(PAL_TEMPLATES)].format(pal=pal),
            [pal], band)

    for i, pal in enumerate(variants[:12]):
        add("utterance", VARIANT_TEMPLATES[i % len(VARIANT_TEMPLATES)].format(pal=pal),
            [pal], "variant")

    frame_pool = bands["hard"][20:25] + bands["medium"][14:19]
    for i, pal in enumerate(frame_pool[:10]):
        add("utterance", FRAME_WORD_TEMPLATES[i % len(FRAME_WORD_TEMPLATES)].format(pal=pal),
            [pal], "frame_word")

    pair_pool = bands["medium"][19:] + bands["easy"][8:]
    for i, t in enumerate(TWO_PAL_TEMPLATES * 2):
        a, b = pair_pool[i * 2 % len(pair_pool)], pair_pool[(i * 2 + 1) % len(pair_pool)]
        if a == b:
            continue
        add("utterance", t.format(pal=a, pal2=b), [a, b], "two_entity")

    for i in range(12):
        res = RESOURCES[i % len(RESOURCES)]
        canonical = res.replace(" ", "_")
        add("utterance", RESOURCE_TEMPLATES[i % len(RESOURCE_TEMPLATES)].format(res=res),
            [canonical], "resource")

    for t in NO_ENTITY:
        add("utterance", t, [], "no_entity")

    start = max(int(p["id"][1:]) for p in keep)
    for i, p in enumerate(new):
        p["id"] = f"P{start + i + 1:03d}"

    prompts = keep + new
    scored = sum(len(p["expect_entities"]) for p in prompts)
    utterances = sum(1 for p in prompts if p["group"] == "utterance")

    (OUT / "prompts.json").write_text(json.dumps({
        "version": 3,
        "lexicon_version": lex["lexicon_version"],
        "game_version": lex["game_version"],
        "seed": SEED,
        "count": len(prompts),
        "new_in_v3": len(new),
        "utterances": utterances,
        "scored_entities": scored,
        "prompts": prompts,
    }, indent=2), encoding="utf-8")

    print(f"v3: {len(keep)} kept + {len(new)} new = {len(prompts)} prompts "
          f"({utterances} utterances, {scored} scored entities)")
    by = {}
    for p in new:
        by[p["difficulty"]] = by.get(p["difficulty"], 0) + 1
    for k in sorted(by):
        print(f"    new {k:12s} {by[k]:3d}")
    print(f"\n  record with:  python tools/eval/record_stt.py --condition quiet")
    print(f"  it will skip the {len(keep)} already in the manifest and ask for "
          f"{len(new)}.")


if __name__ == "__main__":
    main()
