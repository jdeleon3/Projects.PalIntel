"""Generate the STT evaluation prompt set (assumption A5).

v2 - utterance-weighted.

v1 put 20 of 28 prompts on isolated bare names. Nobody speaks that way to this system;
they say "where can I find Lifmunk", not "Lifmunk". Bare names are the hardest possible
case for an acoustic model and the condition never occurs in production, so v1's headline
number was dominated by an artificial task while the group that matters carried only five
scored entities.

v2 inverts the weighting: ~40 utterances carrying 1-2 entities each, sampled across the
full difficulty range, plus a small bare-name control group retained purely for
diagnosis. If controls fail, the problem is the audio pipeline rather than vocabulary.

Output: data/stt_eval/prompts.json
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LEXICON = REPO / "data" / "1.0.2" / "lexicon.json"
OUT = REPO / "data" / "stt_eval"

SEED = 20260808  # fixed so the set is reproducible across re-runs

CONTROLS = ["Anubis", "Foxparks", "Lamball", "Grizzbolt"]

# Templates span the query classes the router must handle, so entity recognition is
# tested in the same phrasings production will see.
PAL_TEMPLATES = [
    "hey pal where can I find {pal}",
    "hey pal where do {pal} spawn",
    "hey pal how do I breed {pal}",
    "hey pal what's the breeding combo for {pal}",
    "hey pal is {pal} good for mining",
    "hey pal what element is {pal}",
    "hey pal should I use {pal} against the first tower",
    "hey pal where's the nearest {pal}",
]
TWO_PAL_TEMPLATES = [
    "hey pal can I breed {pal} with {pal2}",
    "hey pal is {pal} better than {pal2} for handiwork",
]
RESOURCE_TEMPLATES = [
    "hey pal where's the nearest {res}",
    "hey pal find me a {res} spot for level twenty",
    "hey pal where's the closest {res} deposit",
    "hey pal show me {res} near my base",
]
NO_ENTITY = [
    "hey pal what should I research next",
    "hey pal where should I put my second base",
    "hey pal what does the artisan trait do",
]

RESOURCES = ["coal", "ore", "sulfur", "quartz"]


def unusual(name: str) -> int:
    w = re.sub(r"[^a-z]", "", name.lower())
    rare = sum(1 for a, b in zip(w, w[1:]) if a + b in {
        "fm", "mn", "rm", "tz", "zz", "kt", "gp", "wy", "jo", "xt", "vy", "nk", "th"})
    return len(w) + rare * 3


def main() -> None:
    rng = random.Random(SEED)
    lex = json.loads(LEXICON.read_text(encoding="utf-8"))
    pals = [p["canonical"] for p in lex["pals"]
            if p["in_paldeck"] and " " not in p["canonical"]]

    # Stratify by difficulty so the set is not accidentally all-easy or all-hard.
    ranked = sorted(pals, key=unusual)
    third = len(ranked) // 3
    bands = {"easy": ranked[:third], "medium": ranked[third:2 * third], "hard": ranked[2 * third:]}
    for b in bands.values():
        rng.shuffle(b)

    picks: list[tuple[str, str]] = []
    for band in ("hard", "hard", "medium", "easy"):  # weight toward hard
        picks += [(band, n) for n in bands[band][:9]]
    rng.shuffle(picks)

    prompts: list[dict] = []

    for n in CONTROLS:
        prompts.append({"group": "control", "text": n, "expect_entities": [n],
                        "difficulty": "control"})

    ti = 0
    used: set[str] = set()
    for band, pal in picks:
        if pal in used:
            continue
        used.add(pal)
        t = PAL_TEMPLATES[ti % len(PAL_TEMPLATES)]
        ti += 1
        prompts.append({"group": "utterance", "text": t.format(pal=pal),
                        "expect_entities": [pal], "difficulty": band})
        if len(prompts) >= 34:
            break

    pool = [p for _, p in picks if p not in used] or [p for _, p in picks]
    for i, t in enumerate(TWO_PAL_TEMPLATES):
        a, b = pool[i * 2 % len(pool)], pool[(i * 2 + 1) % len(pool)]
        prompts.append({"group": "utterance", "text": t.format(pal=a, pal2=b),
                        "expect_entities": [a, b], "difficulty": "two_entity"})

    for i, t in enumerate(RESOURCE_TEMPLATES):
        res = RESOURCES[i % len(RESOURCES)]
        text = t.format(res=res)
        # "a ore spot" reads wrong aloud, and prompts must be natural to say or the
        # recording tests the reader's stumble rather than the model.
        text = re.sub(r"\ba (?=[aeiou])", "an ", text)
        prompts.append({"group": "utterance", "text": text,
                        "expect_entities": [res], "difficulty": "resource"})

    for t in NO_ENTITY:
        prompts.append({"group": "utterance", "text": t, "expect_entities": [],
                        "difficulty": "no_entity"})

    for i, p in enumerate(prompts):
        p["id"] = f"P{i + 1:02d}"

    scored = sum(len(p["expect_entities"]) for p in prompts)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "prompts.json").write_text(json.dumps({
        "version": 2,
        "lexicon_version": lex["lexicon_version"],
        "game_version": lex["game_version"],
        "seed": SEED,
        "count": len(prompts),
        "scored_entities": scored,
        "prompts": prompts,
    }, indent=2), encoding="utf-8")

    print(f"{len(prompts)} prompts, {scored} scored entities -> {OUT / 'prompts.json'}")
    print("(v1 had 28 prompts but only 5 scored entities in the group that matters)\n")
    for g in ("control", "utterance"):
        rows = [p for p in prompts if p["group"] == g]
        print(f"{g} ({len(rows)}):")
        for p in rows:
            print(f"   {p['id']}  [{p['difficulty']:<9}] {p['text']}")
        print()


if __name__ == "__main__":
    main()
