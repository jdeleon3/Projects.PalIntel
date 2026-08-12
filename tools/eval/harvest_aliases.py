"""Harvest alias candidates from recorded misses, and measure what they would buy.

`build_lexicon.py` says SEED_ALIASES "is expected to GROW from observed failures - every
misrecognition found in evaluation becomes a permanent alias". This is that loop, run
against the recordings that already exist rather than against new ones.

**Why aliases and not the acceptance floor.** Swept on 2026-08-11 across both sets, the
floor buys one extra hit on the branch batch for two wrong entities on the 240, and four
hits for three. A wrong card is the trade this project refuses in that direction. An
alias is surgical where the floor is global: it raises one true match to 1.0 and loosens
nothing else.

The candidates come from the mangled surface form the corrector ALREADY matched on -
Vanwyrm scored 0.71 against "fan worm", so "fan worm" is the alias, taken from the data
rather than invented. Entities that did not rank at all yield no surface form and are
reported separately; guessing one would be writing fiction into the lexicon.

**Four checks, because a bad alias is worse than a missing one.** A candidate is rejected
if it already resolves confidently (nothing to fix), if it fails `safe_aliases` for being
too short or too common, if it resolves to a DIFFERENT Pal at least as well (the Woolipop
rule - a matcher loose enough to join those would join things that must stay apart), or
if it appears in recordings that never named that Pal.

The fourth is necessary and demonstrably not sufficient. "dragon" is a fair surface form
for Jetragon in one utterance and a disaster as a permanent alias - and it appears in
ZERO other recordings, because 227 clips of Palworld questions contain no general speech.
Candidates built entirely from ordinary words are therefore HELD FOR REVIEW rather than
accepted: the corpus cannot clear them, and a permanent alias meets every sentence the
player ever says.

Nothing is written. The output is a reviewable block to paste into SEED_ALIASES, because
which manglings deserve permanence is a judgement about one speaker's voice.

Usage: python tools/eval/harvest_aliases.py --condition quiet
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools" / "ingest"))

from palintel.knowledge import KnowledgeBase          # noqa: E402
from palintel.routing import PAL_CONFIDENT            # noqa: E402

EVAL = REPO / "data" / "stt_eval"

# Words a player says for reasons that have nothing to do with a Pal. Small and
# deliberately not a dictionary: the point is not to catch every English word, it is to
# stop the obviously-unsafe ones being auto-accepted from a corpus that cannot judge
# them. Anything here lands in REVIEW, not REJECTED - a human may still want it.
# Grammatical glue. Rejected if it appears ANYWHERE in an alias, not only alone,
# because these are the words that sit between other words - which is what turns a
# real mangling into a phrase that matches sentences it has nothing to do with.
FUNCTION_WORDS = {
    "a", "an", "and", "at", "do", "for", "from", "i", "in", "is", "it", "me", "my",
    "of", "on", "or", "some", "that", "the", "their", "them", "these", "this", "to",
    "what", "when", "where", "which", "who", "why", "with", "you", "your",
}

ORDINARY_WORDS = {
    "a", "an", "and", "any", "are", "at", "beacon", "best", "can", "creates", "do",
    "dragon", "find", "for", "from", "get", "go", "good", "has", "have", "how", "i",
    "in", "is", "it", "level", "like", "me", "my", "near", "needle", "of", "on", "one",
    "or", "primo", "should", "some", "that", "the", "their", "them", "there", "these",
    "this", "to", "up", "use", "what", "when", "where", "which", "who", "why", "with",
    "you", "your", "swat", "surfing", "hilarious", "duelist", "lumen", "beat", "beats",
    "fight", "kill", "counter", "counters", "defeat", "drop", "drops",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", default="quiet")
    ap.add_argument("--session", default="",
                    help="harvest a GAMEPLAY session instead of the scripted set. Reads "
                         "the analysis.json that tools/eval/analyse_session.py --write "
                         "produces, whose expectations come from rephrase proposals "
                         "rather than from a prompt - so they are weaker evidence and "
                         "the output says so.")
    ap.add_argument("--floor", type=float, default=PAL_CONFIDENT)
    args = ap.parse_args()

    from build_lexicon import SEED_ALIASES, safe_aliases

    if args.session:
        # A gameplay session. **The expectations are proposals, not prompts**: nothing
        # told the player what to say, so what an utterance "should" have resolved to is
        # inferred from a later rephrase that worked. The four checks below are unchanged
        # and matter more here, not less.
        path = REPO / "data" / "sessions" / args.session / "analysis.json"
        if not path.exists():
            sys.exit(f"no {path}\n  python tools/eval/analyse_session.py "
                     f"--session {args.session} --write")
        analysis = json.loads(path.read_text(encoding="utf-8"))
        records = analysis["records"]
        prompts = {r["id"]: {"expect_entities": r["expected"]} for r in records}
        print(f"gameplay session {analysis['session']}: {len(records)} rephrase "
              f"proposals, {len(analysis.get('unresolved', []))} unresolved\n")
    else:
        prompts = {p["id"]: p for p in
                   json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]}
        results = json.loads(
            (EVAL / args.condition / "results.json").read_text(encoding="utf-8"))
        records = list(results.values() if isinstance(results, dict) else results)

    kb = KnowledgeBase.load("1.0.2")
    known = {c: {a.lower() for a in SEED_ALIASES.get(c, [])} for c in SEED_ALIASES}

    candidates: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    no_surface: dict[str, int] = defaultdict(int)
    scanned = 0

    for rec in records:
        p = prompts.get(rec.get("id"))
        text = rec.get("boosted_text") or rec.get("text")
        if not p or not text:
            continue
        want = p.get("expect_entities") or []
        if not want:
            continue
        scanned += 1
        ranked = kb.lexicon.rank(text)
        by_name = {c.canonical: c for c in ranked}
        for name in want:
            c = by_name.get(name)
            if c is None:
                # Never ranked: there is no surface form to learn from, and inventing
                # one would put fiction in the lexicon.
                no_surface[name] += 1
                continue
            if c.score >= args.floor:
                continue                       # already resolves; nothing to fix
            surface = (c.matched_text or "").strip().lower()
            if surface and surface != name.lower():
                candidates[name][surface] += 1

    # --- safety
    accepted: dict[str, list[str]] = defaultdict(list)
    review: dict[str, list[tuple[str, str]]] = defaultdict(list)
    rejected: list[tuple[str, str, str]] = []
    for name, forms in candidates.items():
        for surface in forms:
            if surface in known.get(name, set()):
                rejected.append((name, surface, "already an alias"))
                continue
            if not safe_aliases([surface]):
                rejected.append((name, surface, "too short or too common"))
                continue
            # Does this string already belong to somebody else? Rank it alone.
            solo = kb.lexicon.rank(surface)
            top = solo[0] if solo else None
            if top and top.canonical != name and top.score >= args.floor:
                rejected.append((name, surface,
                                 f"resolves to {top.canonical} at {top.score:.2f}"))
                continue

            # **The check that matters, and a wordlist would not have caught it.**
            # "dragon" is a fair surface form for Jetragon in one utterance and a
            # disaster as a permanent alias, because it appears in sentences about
            # other Pals, about the Dragon element, and about nothing at all. So ask
            # the corpus: how many recordings contain this string WITHOUT naming this
            # Pal? build_lexicon.py makes the same point with "ore" matching "for".
            intruded = sum(
                1 for r in records
                if surface in ((r.get("boosted_text") or r.get("text") or "").lower())
                and name not in (prompts.get(r.get("id"), {}).get("expect_entities") or [])
            )
            if intruded:
                rejected.append((name, surface,
                                 f"appears in {intruded} recording(s) that do not name it"))
                continue

            # **The corpus check is necessary and not sufficient, and this is the proof:**
            # "dragon" as an alias for Jetragon appears in ZERO other recordings, because
            # 227 clips of Palworld questions contain no general speech. Absence of
            # evidence in a corpus this narrow is not evidence of safety for a word this
            # common - and a permanent alias meets every sentence the player ever says,
            # not just these.
            #
            # So anything built entirely from ordinary words is held for review rather
            # than accepted. Note "defeat" already ranks as Felbat at 0.67, so cue
            # vocabulary is in this space too.
            toks = surface.split()
            if any(t in FUNCTION_WORDS for t in toks):
                # **Grammatical glue is the dangerous kind, and a whole-string check
                # misses it.** "and cryst" scored 1.00 for Vanwyrm Cryst - correct for
                # the one recording it came from, and unsafe forever after, because
                # "and" sits between any two words: "where can I find Mau and Cryst"
                # would resolve confidently to the wrong Pal. Caught by an existing
                # test that pins exactly this ("a mangled Pal name defers instead of
                # guessing"), which is the second time today the corpus said yes and
                # something else said no.
                review[name].append((surface, "contains a function word"))
                continue
            if all(t in ORDINARY_WORDS for t in toks):
                review[name].append((surface, "made of ordinary words"))
                continue
            if args.session:
                # **Nothing from a gameplay session is auto-accepted, and this rule was
                # earned on the first run of it.**
                #
                # The four checks above validate the SURFACE FORM - is it too short, too
                # common, does it collide with another Pal. They say nothing about
                # whether the TARGET is right, because on the scripted set the target
                # comes from a prompt and is not in question. On a session it is
                # inferred from a rephrase, and the first run accepted
                # `majoran -> Bjorn` outright: a proposal that a human had already
                # confirmed as *misheard* but never confirmed as *Bjorn*, sitting at 0.73
                # frame similarity against a real pair at 0.70.
                #
                # A misheard button says the transcript was wrong. It does not say what
                # was meant, and nothing in this file can tell the two apart.
                review[name].append((surface, "target inferred from a rephrase, "
                                              "not stated by a prompt"))
                continue
            accepted[name].append(surface)

    print(f"scanned {scanned} recordings with expected entities\n")
    print(f"ACCEPTED - {sum(len(v) for v in accepted.values())} candidates "
          f"across {len(accepted)} Pals\n")
    for name in sorted(accepted):
        forms = sorted(accepted[name], key=lambda f: -candidates[name][f])
        counts = ", ".join(f"{f!r} x{candidates[name][f]}" for f in forms)
        print(f"  {name:<18} {counts}")

    if accepted:
        print("\npaste into SEED_ALIASES in tools/ingest/build_lexicon.py:\n")
        for name in sorted(accepted):
            forms = sorted(accepted[name], key=lambda f: -candidates[name][f])
            existing = SEED_ALIASES.get(name, [])
            merged = existing + [f for f in forms if f not in existing]
            print(f'    "{name}": {json.dumps(merged)},')

    if review:
        # **This block did not exist until 2026-08-12.** The `review` pile was collected
        # by three separate branches, described in the module docstring as "HELD FOR
        # REVIEW rather than accepted", and then never printed - so every run of this
        # tool silently discarded exactly the candidates a human was supposed to judge.
        # Found by adding a fourth branch and seeing its output vanish.
        print(f"\nHELD FOR REVIEW - {sum(len(v) for v in review.values())} candidates "
              f"across {len(review)} Pals")
        print("  (a judgement, not a rejection - these are the ones worth your eyes)")
        for name in sorted(review):
            for surface, why in sorted(review[name]):
                print(f"  {name:<18} {surface!r:<24} {why}")

    if rejected:
        print(f"\nREJECTED - {len(rejected)}")
        for name, surface, why in sorted(rejected):
            print(f"  {name:<18} {surface!r:<24} {why}")

    if no_surface:
        print(f"\nNO SURFACE FORM - never ranked at all, so nothing to learn from:")
        for name, n in sorted(no_surface.items(), key=lambda kv: -kv[1])[:12]:
            print(f"  {name:<18} x{n}")

    print(f"\nfloor {args.floor}. Re-run build_lexicon.py after pasting, then "
          f"score_branches.py and score_fast_path.py to measure the change.")


if __name__ == "__main__":
    main()
