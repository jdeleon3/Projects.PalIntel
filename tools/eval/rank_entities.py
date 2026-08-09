"""Measure whether the entity signal survives transcription at all (A5 diagnosis).

score_stt.py measures threshold-and-decline matching: it answers "is this match good
enough to trust?". The intent router works differently - it is FORCED CHOICE over a
constrained enum, and it has sentence context ("should I use ___ against the first
tower" says the slot is a Pal).

This tool isolates the forced-choice half, without needing a model. For each utterance
it ranks all lexicon entities by similarity to the transcript and reports where the
correct answer lands.

The rank distribution is the actual diagnosis:
  - correct answer mostly rank 1-3  -> the signal survived; a context-aware router
                                       should recover most of the gap, and A5 is
                                       likely salvageable
  - correct answer deep in the list -> transcription destroyed the signal, and no
                                       downstream layer can reconstruct it. ADR-0007's
                                       redesign trigger fires for real.

Usage: python tools/eval/rank_entities.py --condition quiet
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score_stt import load_lexicon, squash  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "data" / "stt_eval"

# Words that carry the query frame rather than the entity. Excluded from candidate
# n-grams so the ranking is not dominated by template boilerplate.
FRAME = {
    "hey", "pal", "pals", "where", "can", "i", "find", "do", "how", "breed", "the",
    "for", "is", "good", "at", "what", "element", "should", "use", "against", "first",
    "tower", "nearest", "closest", "my", "me", "a", "an", "with", "better", "than",
    "spawn", "combo", "breeding", "of", "s", "near", "base", "show", "level", "twenty",
    "deposit", "spot", "research", "next", "put", "second", "does", "trait", "mining",
    "handiwork", "counter", "which",
}


def candidates(text: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z']+", text.lower())]
    grams = []
    for n in (1, 2, 3):
        for i in range(len(words) - n + 1):
            gram = words[i:i + n]
            if all(w in FRAME for w in gram):
                continue
            grams.append(" ".join(gram))
    return grams


def best_score(gram: str, surfaces: list[str]) -> float:
    g, gs = gram, squash(gram)
    return max(max(SequenceMatcher(None, g, s).ratio(),
                   SequenceMatcher(None, gs, squash(s)).ratio())
               for s in surfaces)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True)
    args = ap.parse_args()

    src = EVAL / args.condition
    results = json.loads((src / "results.json").read_text(encoding="utf-8"))
    forms = load_lexicon()

    ranks: list[tuple[str, str, int, str]] = []
    for r in results:
        if not r["expected"]:
            continue
        text = r["boosted_text"]
        grams = candidates(text)
        if not grams:
            continue
        scored = sorted(
            ((canon, max(best_score(g, surfaces) for g in grams))
             for canon, surfaces in forms.items()),
            key=lambda kv: -kv[1])
        order = [c for c, _ in scored]
        for want in r["expected"]:
            rank = order.index(want) + 1 if want in order else 9999
            ranks.append((r["id"], want, rank, order[0]))

    print(f"condition={args.condition}   entities={len(ranks)}\n")
    print(f"{'id':<5}{'expected':<14}{'rank':>6}   top-1 candidate")
    for pid, want, rank, top in sorted(ranks, key=lambda x: x[2]):
        mark = "" if rank == 1 else ("  <-- " + top if rank > 3 else "")
        print(f"{pid:<5}{want:<14}{rank:>6}{mark}")

    n = len(ranks)
    for k in (1, 3, 5, 10):
        hit = sum(1 for _, _, r, _ in ranks if r <= k)
        print(f"\n  top-{k:<3} {hit}/{n} = {hit / n * 100:5.1f}%", end="")
    deep = [x for x in ranks if x[2] > 10]
    print(f"\n\n  beyond top-10: {len(deep)}/{n}"
          f"  -> {[f'{p}:{w}@{r}' for p, w, r, _ in deep][:8]}")

    top1 = sum(1 for _, _, r, _ in ranks if r == 1) / n * 100
    top3 = sum(1 for _, _, r, _ in ranks if r <= 3) / n * 100
    print("\n" + "=" * 66)
    if top3 >= 85:
        print("SIGNAL SURVIVES. The correct entity is nearly always a top-3 candidate,")
        print("so a context-aware forced-choice router should recover most of the gap.")
    elif top3 >= 60:
        print("SIGNAL PARTLY SURVIVES. A router would help materially but likely not")
        print("reach 95% alone; consider stronger keyterm boosting as well.")
    else:
        print("SIGNAL DESTROYED. Transcription is losing the entity outright, so no")
        print("downstream layer can reconstruct it. ADR-0007 redesign trigger applies.")
    print(f"(top-1 {top1:.1f}%, top-3 {top3:.1f}%)")
    print("=" * 66)


if __name__ == "__main__":
    main()
