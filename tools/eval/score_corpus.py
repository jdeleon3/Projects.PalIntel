"""Score the Tier 3 corpus lookup. No model, no cost.

The roadmap asks for the relevance threshold to be "calibrated against a 50-question eval
set split between in-corpus and out-of-corpus questions". **This is not that set.** It is
28 questions written by hand while building the class, so it is the developer's guess at
what a player asks, tuned on and held out from nothing.

It is still worth running, for the same reason `score_fast_path.py` is: what matters is
**precision, not coverage**. An out-of-corpus question answered anyway is a quote from
the game presented as the answer to a question it does not address - confidently wrong,
with a citation making it look more trustworthy rather than less. An in-corpus question
declined is only a missed answer.

So the floor is chosen by where wrong answers start, exactly as `BACKSTOP_CONFIDENT` and
`PAL_CONFIDENT` were, and the number it produces is provisional until real play supplies
questions nobody wrote down in advance.

Usage:
    python tools/eval/score_corpus.py           # the shipping floor
    python tools/eval/score_corpus.py --sweep   # every floor, both error kinds
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel import corpus  # noqa: E402

# (question, the section the right answer lives in, or None for out-of-corpus).
#
# The out-of-corpus half is the important half and it is deliberately not silly. "What is
# the capital of France" is trivial to decline; the questions that matter are ones a
# Palworld player would really ask that this corpus genuinely cannot answer, because the
# game explains its mechanics and says nothing about playing well.
QUESTIONS: list[tuple[str, str | None]] = [
    # --- in corpus: the game explains these itself
    ("how does sanity work", "Help guide"),
    ("what is item rot", "Help guide"),
    ("how do elements work", "Help guide"),
    ("what are pal souls", "Help guide"),
    ("what are pal effigies", "Help guide"),
    ("how does the breeding farm work", "Help guide"),
    ("what is a lucky pal", "Help guide"),
    ("what are predator pals", "Help guide"),
    ("how does fishing work", "Help guide"),
    ("what does the arena do", "Help guide"),
    ("how does the palbox work", "Help guide"),
    ("what is ancient technology", "Help guide"),
    ("what is the great eagle statue", "Help guide"),
    ("how does crime work", "Help guide"),
    ("what are status effects", "Help guide"),
    ("how does equipment durability work", "Help guide"),
    ("what is the global palbox", "Help guide"),
    # --- in corpus, but PARAPHRASED. The game has a "Death" entry and the player says
    # "die"; it has "Pal Rank & Essence Condensers" and the player says "raise". These
    # are the questions that decide whether a lexical scorer is enough, and they are here
    # because the seventeen above were written using the help guide's own words - which
    # is the tuning bias this file cannot remove by being run again.
    ("what happens when I die", "Help guide"),
    ("how do I raise a pal's rank", "Help guide"),
    ("what makes my pals unhappy", "Help guide"),
    ("do my pals get tired", "Help guide"),
    ("can my food go bad", "Help guide"),
    # --- out of corpus: real questions the game's own text does not answer
    ("what's the best base layout", None),
    ("which pal has the highest attack stat", None),
    ("what's the fastest way to level up to 60", None),
    ("should I use a shotgun or a rifle", None),
    ("what's the best breeding combo for Anubis", None),
    ("how many players can join a server", None),
    ("what's the most efficient ore farm", None),
    # --- out of corpus and not about Palworld at all, as a floor on the floor
    ("what is the capital of France", None),
    ("how do I make a sandwich", None),
    ("how much does a car cost", None),
    ("who won the world cup", None),
]


def score(floor: float) -> tuple[int, int, int, int, list[str]]:
    """(answered right, declined wrongly, answered wrongly, declined right, notes)."""
    right = missed = wrong = declined = 0
    notes = []
    for question, section in QUESTIONS:
        result = corpus.load().search(question, limit=1, floor=floor)
        got = result.passages[0] if result.grounded else None
        if section is None:
            if got is None:
                declined += 1
            else:
                # The failure that matters: a quote from the game, cited, answering a
                # question it does not address.
                wrong += 1
                notes.append(f"  WRONG {got.score:.2f}  {question!r} -> "
                             f"{got.chunk.citation}")
        elif got is None:
            missed += 1
            notes.append(f"  missed {result.best_score:.2f}  {question!r}")
        elif got.chunk.section == section:
            right += 1
        else:
            # Answered from the wrong KIND of source - a Paldeck flavour line where the
            # help guide explains the mechanic. Counted as wrong, not as a near miss.
            wrong += 1
            notes.append(f"  WRONG {got.score:.2f}  {question!r} -> "
                         f"{got.chunk.citation} (wanted {section})")
    return right, missed, wrong, declined, notes


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    in_corpus = sum(1 for _, s in QUESTIONS if s)
    print(f"{len(QUESTIONS)} questions: {in_corpus} in corpus, "
          f"{len(QUESTIONS) - in_corpus} out\n")

    floors = ([0.30, 0.40, 0.50, 0.62, 0.70, 0.80] if args.sweep
              else [corpus.RELEVANT])
    print(f"  {'floor':>6}{'answered right':>16}{'declined right':>16}"
          f"{'WRONG':>8}{'missed':>8}")
    last = None
    for floor in floors:
        right, missed, wrong, declined, notes = score(floor)
        marker = "  <- shipping" if floor == corpus.RELEVANT else ""
        print(f"  {floor:>6.2f}{right:>16}{declined:>16}{wrong:>8}{missed:>8}{marker}")
        last = (floor, notes)
    if last and last[1]:
        print(f"\nat floor {last[0]:.2f}:")
        for n in last[1]:
            print(n)


if __name__ == "__main__":
    main()
