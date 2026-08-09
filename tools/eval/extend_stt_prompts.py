"""Extend the STT evaluation prompt set in balanced batches (assumption A5).

The set grows over time: record a batch, score it, record another. Target is ~1000
prompts, which is far more than one sitting, so the file has to stay useful at every
intermediate size.

**Every batch is a balanced miniature of the whole.** The composition below is fixed per
batch rather than filled category-by-category across the set, because a partially
recorded set must still be an unbiased sample. If no-entity prompts all lived in the last
batch, then stopping halfway would silently produce a set with no false-positive test and
a headline number that looked fine.

**This script appends. It never regenerates.** Existing prompts are copied through
byte-identical, because record_stt.py skips ids already in a condition's manifest - takes
already on disk stay valid, and only new ids are asked for. Regenerating would invalidate
them the moment the lexicon or sampling changed.

Batch composition (40 prompts - one sitting, the size of the original recorded set).
Each slice is a measured gap rather than a guess; see Docs/04-roadmap.md:

  hard 9 / medium 6 / easy 3   single Pals, weighted toward acoustically hard names
  variant       5              multi-word names ("Kitsun Noct"); where models hedge
  frame_word    4              phrasings whose frame words the corrector mis-matches
  two_entity    4              arity, and the over-naming failure mode
  resource      3              includes crude_oil
  no_entity     6              the false-positive test

Usage:
    python tools/eval/extend_stt_prompts.py --batches 4     # append 4 batches
    python tools/eval/extend_stt_prompts.py --target 1000   # append until ~1000 prompts
    python tools/eval/record_stt.py --condition quiet --batch 2
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEXICON = REPO / "data" / "1.0.2" / "lexicon.json"
OUT = REPO / "data" / "stt_eval"

SEED = 20260809

# Prompts per batch, and the mix. Keep this stable: changing it mid-collection makes
# early and late batches non-comparable, which is the one thing batching exists to avoid.
# 40 is one sitting - the same size as the original recorded set, which took ~15 minutes.
COMPOSITION = [("hard", 9), ("medium", 6), ("easy", 3), ("variant", 5),
               ("frame_word", 4), ("two_entity", 4), ("resource", 3), ("no_entity", 6)]
BATCH_SIZE = sum(n for _, n in COMPOSITION)

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
    "hey pal what does {pal} drop",
    "hey pal how much stamina does {pal} have",
    "hey pal is {pal} any good for logging",
    "hey pal what's a good partner skill for {pal}",
    "hey pal can {pal} carry things back to base",
    "hey pal what level should {pal} be before the tower",
    "hey pal does {pal} work at night",
    "hey pal how rare is {pal}",
]

VARIANT_TEMPLATES = [
    "hey pal where can I find {pal}",
    "hey pal what element is {pal}",
    "hey pal how do I breed {pal}",
    "hey pal is {pal} better than the normal one",
    "hey pal what does {pal} drop",
    "hey pal is {pal} worth catching",
    "hey pal where do {pal} spawn at night",
    "hey pal how do I get {pal}",
]

# Frame words the corrector mis-matches against real Pal names: "show me" scores 0.71
# against Shroomer, "against the" 0.59 against Maraith. Those spurious candidates are
# what the local model selected, so the set needs them deliberately, not by accident.
FRAME_WORD_TEMPLATES = [
    "hey pal show me {pal} near my base",
    "hey pal is {pal} any good against the first tower",
    "hey pal can you find me a {pal} somewhere close",
    "hey pal what's the best way to catch {pal}",
    "hey pal do I need a better sphere for {pal}",
    "hey pal find me a {pal} near my second base",
    "hey pal show me where {pal} are right now",
    "hey pal is it worth going after {pal} tonight",
    "hey pal can I get {pal} before the first tower",
    "hey pal help me find {pal} around here",
]

TWO_PAL_TEMPLATES = [
    "hey pal can I breed {pal} with {pal2}",
    "hey pal is {pal} better than {pal2} for handiwork",
    "hey pal which should I use {pal} or {pal2}",
    "hey pal do {pal} and {pal2} make anything good",
    "hey pal compare {pal} and {pal2} for combat",
    "hey pal should I put {pal} or {pal2} on the ranch",
    "hey pal what do I get from {pal} and {pal2}",
    "hey pal is {pal} faster than {pal2}",
]

RESOURCE_TEMPLATES = [
    "hey pal where's the nearest {res}",
    "hey pal find me a {res} spot for level twenty",
    "hey pal where's the closest {res} deposit",
    "hey pal show me {res} near my base",
    "hey pal is there any {res} around here",
    "hey pal I need {res} for a new base",
    "hey pal where do I mine {res}",
    "hey pal what's the best {res} node nearby",
    "hey pal how far is the nearest {res}",
    "hey pal can I get {res} at this level",
    "hey pal show me a safe {res} spot",
    "hey pal where should I set up for {res}",
    "hey pal is there {res} near the first tower",
    "hey pal what's the closest place to farm {res}",
    "hey pal do I have enough {res} for this",
    "hey pal point me at some {res}",
    "hey pal any {res} worth mining nearby",
]

# Only resources the locate tool can actually name. crude_oil is in the lexicon but has
# no extracted map nodes, so it never enters the tool's enum - 17 prompts asking for it
# were unanswerable by construction, and the router's correct declines were being scored
# as misses. Derived from the knowledge base rather than listed, so a resource gaining or
# losing node data cannot silently reintroduce the same bug.
def _locatable_resources() -> list[str]:
    import sys
    sys.path.insert(0, str(REPO))
    from palintel.knowledge import KnowledgeBase
    kb = KnowledgeBase.load("1.0.2")
    return sorted({n.resource for n in kb.nodes})

# The false-positive test, built combinatorially so it can supply ~120 distinct prompts
# without hand-writing them. Topics are deliberately entity-shaped nouns - traits, tech,
# towers - because an entity-shaped noun is what provokes a hallucinated match.
NO_ENTITY_FRAMES = [
    "hey pal what should I do about {topic}",
    "hey pal how do I deal with {topic}",
    "hey pal is it worth worrying about {topic}",
    "hey pal what's the best approach to {topic}",
    "hey pal can you explain {topic}",
    "hey pal how important is {topic}",
    "hey pal what do I need for {topic}",
    "hey pal should I focus on {topic} yet",
    "hey pal how do I get better at {topic}",
    "hey pal what changes with {topic}",
]
NO_ENTITY_TOPICS = [
    "my next research", "my second base", "the artisan trait", "my capture rate",
    "levelling up faster", "upgrading my gear", "the worker limit", "the lucky trait",
    "a second breeding pen", "technology points", "the first tower", "pals getting depressed",
    "my weapon choice", "feeding overnight", "the next tier of gear", "base defence",
    "raid attacks", "my guild setup", "fast travel points", "cooking food",
    "the pal box limit", "sanity in the base", "hot and cold weather", "my stat points",
]


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


def _interleave(pairs: list[tuple[str, list[str], int]],
                rng: random.Random) -> list[tuple[str, list[str]]]:
    """Round-robin the pool by template so consecutive draws use different phrasings.

    A plain shuffle lets a batch draw the same sentence frame several times in a row -
    tedious to read aloud, and it narrows how many phrasings a batch actually tests.
    Dealing one template at a time spreads them evenly at every batch size.
    """
    buckets: dict[int, list[tuple[str, list[str]]]] = {}
    for text, ents, ti in pairs:
        buckets.setdefault(ti, []).append((text, ents))
    for b in buckets.values():
        rng.shuffle(b)
    order = sorted(buckets)
    rng.shuffle(order)
    out: list[tuple[str, list[str]]] = []
    while any(buckets[t] for t in order):
        for t in order:
            if buckets[t]:
                out.append(buckets[t].pop())
    return out


def _pools(lex: dict, rng: random.Random) -> dict[str, list[tuple[str, list[str]]]]:
    """Every (text, entities) pair the generator may draw, ordered for even coverage.

    Built as an exhausted-in-order pool rather than sampled per batch so that no text can
    repeat across the whole 1000-prompt set, and so coverage spreads evenly instead of
    clustering by luck.
    """
    simple = [p["canonical"] for p in lex["pals"]
              if p["in_paldeck"] and " " not in p["canonical"]]
    variants = [p["canonical"] for p in lex["pals"]
                if p["in_paldeck"] and " " in p["canonical"]]

    ranked = sorted(simple, key=unusual)
    third = len(ranked) // 3
    bands = {"easy": ranked[:third], "medium": ranked[third:2 * third],
             "hard": ranked[2 * third:]}

    raw: dict[str, list[tuple[str, list[str], int]]] = {}
    for band, names in bands.items():
        raw[band] = [(t.format(pal=n), [n], i)
                     for n in names for i, t in enumerate(PAL_TEMPLATES)]
    raw["variant"] = [(t.format(pal=n), [n], i)
                      for n in variants for i, t in enumerate(VARIANT_TEMPLATES)]
    raw["frame_word"] = [(t.format(pal=n), [n], i)
                         for n in bands["hard"] + bands["medium"]
                         for i, t in enumerate(FRAME_WORD_TEMPLATES)]
    raw["resource"] = [(t.format(res=r), [r.replace(" ", "_")], i)
                       for r in _locatable_resources()
                       for i, t in enumerate(RESOURCE_TEMPLATES)]
    raw["no_entity"] = [(f.format(topic=t), [], i)
                        for t in NO_ENTITY_TOPICS for i, f in enumerate(NO_ENTITY_FRAMES)]

    pairs = []
    pool = bands["medium"] + bands["easy"]
    for i, a in enumerate(pool):
        b = pool[(i * 7 + 3) % len(pool)]  # coprime stride: every name pairs widely
        if a != b:
            pairs += [(t.format(pal=a, pal2=b), [a, b], j)
                      for j, t in enumerate(TWO_PAL_TEMPLATES)]
    raw["two_entity"] = pairs

    return {k: _interleave(v, rng) for k, v in raw.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batches", type=int, default=0, help="how many batches to append")
    ap.add_argument("--target", type=int, default=0,
                    help="append batches until the set reaches about this many prompts")
    args = ap.parse_args()

    lex = json.loads(LEXICON.read_text(encoding="utf-8"))
    existing = json.loads((OUT / "prompts.json").read_text(encoding="utf-8"))
    keep = existing["prompts"]

    n_batches = args.batches
    if args.target:
        n_batches = max(0, round((args.target - len(keep)) / BATCH_SIZE))
    if n_batches <= 0:
        raise SystemExit("nothing to do: pass --batches N or --target N")

    # Tag anything untagged so batch numbering is continuous from here on. The original
    # 40 are batch 0 and the first extension batch 1.
    for p in keep:
        p.setdefault("batch", 0 if int(p["id"][1:]) <= 40 else 1)
    next_batch = max(p["batch"] for p in keep) + 1

    rng = random.Random(SEED + next_batch)
    pools = _pools(lex, rng)
    seen = {p["text"] for p in keep}
    cursor = {k: 0 for k in pools}

    new: list[dict] = []
    for b in range(next_batch, next_batch + n_batches):
        for difficulty, count in COMPOSITION:
            pool, taken = pools[difficulty], 0
            while taken < count and cursor[difficulty] < len(pool):
                text, ents = pool[cursor[difficulty]]
                cursor[difficulty] += 1
                text = natural(text)
                if text in seen:
                    continue
                seen.add(text)
                new.append({"group": "utterance", "text": text,
                            "expect_entities": ents, "difficulty": difficulty,
                            "batch": b})
                taken += 1
            if taken < count:
                raise SystemExit(
                    f"pool '{difficulty}' exhausted in batch {b} ({taken}/{count}). "
                    f"Add templates or entities before generating more batches - "
                    f"silently shipping a short batch would unbalance the set.")

    start = max(int(p["id"][1:]) for p in keep)
    for i, p in enumerate(new):
        p["id"] = f"P{start + i + 1:03d}"

    prompts = keep + new
    utterances = sum(1 for p in prompts if p["group"] == "utterance")
    batches = sorted({p["batch"] for p in prompts})

    (OUT / "prompts.json").write_text(json.dumps({
        "version": 4,
        "lexicon_version": lex["lexicon_version"],
        "game_version": lex["game_version"],
        "seed": SEED,
        "batch_size": BATCH_SIZE,
        "batches": len(batches),
        "count": len(prompts),
        "utterances": utterances,
        "scored_entities": sum(len(p["expect_entities"]) for p in prompts),
        "prompts": prompts,
    }, indent=2), encoding="utf-8")

    print(f"appended {n_batches} batches ({len(new)} prompts) -> "
          f"{len(prompts)} total, {utterances} utterances")
    for b in batches:
        rows = [p for p in prompts if p["batch"] == b]
        print(f"    batch {b:>2}  {len(rows):>3} prompts")
    print(f"\n  record one batch at a time:")
    print(f"    python tools/eval/record_stt.py --condition quiet --batch {next_batch}")


if __name__ == "__main__":
    main()
