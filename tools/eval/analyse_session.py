"""Read a gameplay capture session: failure runs, rephrase pairs, alias candidates.

`capture.py` has written clips and a log since 2026-08-11 and **nothing has ever read
them.** The findings STATUS reports from that session were extracted by hand, and it
shows: of the ten manglings that session produced, three reached the lexicon - the one
failure run somebody worked through - and seven are still sitting in the log.

This is the other half. It folds the log, groups the failures, proposes the rephrases,
and hands the result to `harvest_aliases.py`, which already knows how to judge an alias.

## What the design expected, and what the data said

The capture design calls a rephrase **"a free negative label"**: a failed query followed
within ~60s by a similar one that succeeds gives `(bad audio -> correct entity)` at no
interaction cost. Measured against the only session that exists, it is not free.

| pair | frame similarity | verdict |
|---|---|---|
| "beat Exo" -> "beat Axel" | 0.81 | real |
| "about Lening" -> "about Leneen" | 0.88 | real |
| "Gilderoy...drop" -> "Gidra...drop" | 0.72 | real |
| "against Majoran" -> "against Bjorn" | **0.76** | **not a pair** |
| "about Lani" -> "about Orserk" | 0.69 | not a pair |

**The worst false positive scores higher than the best true positive.** Frame similarity
cannot separate them, and neither can similarity to the resolved entity (real pairs
0.29-0.50, false ones up to 0.44). Nothing in the feature set does, on n=1 session.

So this **proposes and does not decide**, which is the same posture `harvest_aliases.py`
already takes for the same reason: *"which manglings deserve permanence is a judgement
about one speaker's voice."* A proposal carries its evidence and its numbers, and a human
accepts it.

## The human feedback is the anchor, not the inference

Nine feedback rows exist in that session - 6 `misheard`, 2 `wrong_entity`, 1
`wrong_class` - and they are ground truth, not the router's opinion of itself. A
`misheard` row says *this transcript was wrong* without any guessing at all, which narrows
the candidate set from 41 utterances to 6 before a single similarity is computed.

That inverts the design's emphasis and is worth stating: **the free label is the button
press, and the rephrase is the hypothesis it makes checkable.**

## Failure runs are counted once

Several attempts at one hard name, none answered, is worth *more* than a single miss -
it is several pronunciations of one word - and it must not skew the corpus by being
counted several times. A run emits `expected: null` unless a later success resolves it;
guessing the name from a run of failures is writing fiction.

Usage:
    python tools/eval/analyse_session.py                  # the newest session
    python tools/eval/analyse_session.py --session 20260811-191709
    python tools/eval/analyse_session.py --write          # also emit analysis.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from palintel.knowledge import STOPWORDS, WAKE_WORDS, squash  # noqa: E402

SESSIONS = REPO / "data" / "sessions"

# How long after a failure a retry still counts as the same attempt.
#
# **90s, from the data rather than the design's ~60s.** The clearest rephrase in the only
# session that exists - "Gilderoy and Dromatite drop" to "what does Gidra and Dromatide
# drop" - is **64 seconds** apart, so a 60s window would miss the best example there is.
# Consecutive gaps in that session run 9s to 130s with the long tail at 125s and 130s
# separating unrelated topics, so 90 sits in the gap between "still trying" and "moved on".
REPHRASE_WINDOW_S = 90.0

# How similar two utterances must be to be the same attempt. Deliberately LOW, because
# the measurement above shows this feature cannot decide on its own - it is a recall
# filter that hands candidates to a human, not a precision gate.
SIMILAR = 0.65

# Feedback that means "the transcript was wrong", which is what an alias fixes. The other
# two kinds - wrong_entity, wrong_class - are routing problems and an alias cannot help.
MISHEARD = "misheard"


@dataclass
class Turn:
    """One captured utterance, with anything a human said about it folded in."""
    uid: str
    at: float
    heard: str
    path: str                      # fast | model | decline
    tool: str | None
    entity: str | None
    outcome: str
    feedback: list[str] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        return self.outcome == "answered"

    @property
    def misheard(self) -> bool:
        """A HUMAN said so. Never inferred - that is the whole value of the button."""
        return MISHEARD in self.feedback

    def content(self) -> set[str]:
        """Words that are not the wake word, punctuation or grammatical glue.

        The same STOPWORDS/WAKE_WORDS the lexicon ranks with, so "what differs between
        these two sentences" means the same thing here as it does to the corrector.
        """
        words = re.findall(r"[a-z0-9']+", self.heard.lower())
        return {w for w in words
                if w not in STOPWORDS and w not in WAKE_WORDS and len(w) > 2}


def load(session_dir: Path) -> list[Turn]:
    """Fold the three row kinds in a capture log into turns.

    The log is append-only - `attach_message` and `record_feedback` write their own lines
    rather than rewriting the utterance, because the answer is long since posted and a
    rewrite would mean holding and replacing the file on the answer path. Folding is the
    reader's job, and this is the reader.
    """
    rows = [json.loads(line) for line in
            (session_dir / "log.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]

    turns = {r["uid"]: Turn(uid=r["uid"], at=r.get("at", 0.0), heard=r.get("heard", ""),
                            path=r.get("path", ""), tool=r.get("tool"),
                            entity=r.get("entity"), outcome=r.get("outcome", ""))
             for r in rows if "heard" in r}
    # message_id -> uid, which is how a button press minutes later finds its clip.
    by_message = {r["message_id"]: r["uid"] for r in rows
                  if set(r) == {"uid", "message_id"}}
    for r in rows:
        if "feedback" not in r:
            continue
        uid = by_message.get(r["message_id"])
        if uid in turns:
            turns[uid].feedback.append(r["feedback"])
    return sorted(turns.values(), key=lambda t: t.at)


def similarity(a: Turn, b: Turn) -> float:
    return SequenceMatcher(None, squash(a.heard), squash(b.heard)).ratio()


def failure_runs(turns: list[Turn]) -> list[list[Turn]]:
    """Consecutive unanswered turns that are attempts at the same thing.

    Grouped so the corpus counts one stubborn question once. A run of three
    pronunciations of "Lyleen" is more informative than one miss and must not be three
    times the weight of one.
    """
    runs: list[list[Turn]] = []
    current: list[Turn] = []
    for turn in turns:
        if turn.answered:
            current = []
            continue
        if current and (turn.at - current[-1].at <= REPHRASE_WINDOW_S
                        and similarity(current[-1], turn) >= SIMILAR):
            current.append(turn)
        else:
            current = [turn]
            runs.append(current)
    return [r for r in runs if r]


@dataclass
class Proposal:
    """A rephrase candidate. **Evidence, not a conclusion.**"""
    failed: Turn
    resolved: Turn
    entity: str
    surface: str
    surface_all: str
    gap_s: float
    frame_similarity: float
    human_confirmed: bool          # a `misheard` button on the failed turn

    @property
    def strength(self) -> str:
        # The button is the only thing here that is not an inference, so it is the only
        # thing that changes the word.
        return "human-confirmed" if self.human_confirmed else "inferred"


def _odd_words(failed: Turn, resolved: Turn, entity: str) -> tuple[str, str]:
    """(the likeliest mangling, everything that differed).

    The whole word-difference is the wrong alias. *"Play Pal with this Gilderoy and
    Dromatite drop"* differs from its retry by `dromatite drop. gilderoy play`, and an
    alias built from that phrase would match sentences it has nothing to do with - the
    exact failure `harvest_aliases.py` holds ordinary-word candidates for review over.

    So the single token closest to the resolved entity is offered as the candidate, and
    the full difference travels beside it because the choice is a guess and a reviewer
    should see what it was chosen from.
    """
    extra = sorted(failed.content() - resolved.content())
    everything = " ".join(extra)
    if not extra:
        return "", ""
    best = max(extra, key=lambda w: SequenceMatcher(None, squash(w),
                                                    squash(entity)).ratio())
    return best, everything


def rephrases(turns: list[Turn]) -> list[Proposal]:
    """Failed turn -> the next similar turn that answered, within the window.

    Every one is a proposal. See the module docstring for the measurement that decided
    that: frame similarity puts a false pair at 0.76 and a true one at 0.72, so no
    threshold here separates them and pretending otherwise would write a wrong alias into
    the lexicon - which `harvest_aliases.py` calls worse than a missing one.
    """
    out: list[Proposal] = []
    for i, failed in enumerate(turns):
        if failed.answered:
            continue
        for later in turns[i + 1:]:
            if later.at - failed.at > REPHRASE_WINDOW_S:
                break
            if not (later.answered and later.entity):
                continue
            sim = similarity(failed, later)
            if sim < SIMILAR:
                continue
            surface, everything = _odd_words(failed, later, later.entity)
            if not surface:
                break
            out.append(Proposal(failed=failed, resolved=later, entity=later.entity,
                                surface=surface, surface_all=everything,
                                gap_s=later.at - failed.at,
                                frame_similarity=round(sim, 2),
                                human_confirmed=failed.misheard))
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="",
                    help="session id; the newest is used when omitted")
    ap.add_argument("--write", action="store_true",
                    help="write analysis.json beside the log, for harvest_aliases.py")
    args = ap.parse_args()

    sessions = sorted(p for p in SESSIONS.glob("*/log.jsonl"))
    if not sessions:
        sys.exit(f"no capture sessions under {SESSIONS}")
    chosen = (SESSIONS / args.session / "log.jsonl") if args.session else sessions[-1]
    if not chosen.exists():
        sys.exit(f"no log at {chosen}")

    turns = load(chosen.parent)
    answered = [t for t in turns if t.answered]
    flagged = [t for t in turns if t.feedback]

    print(f"session {chosen.parent.name}   {len(turns)} utterances, "
          f"{len(list(chosen.parent.glob('*.wav')))} clips")
    print(f"  answered      {len(answered)}   "
          f"({sum(1 for t in answered if t.path == 'fast')} fast, "
          f"{sum(1 for t in answered if t.path == 'model')} model)")
    print(f"  unanswered    {len(turns) - len(answered)}")
    print(f"  human labels  {len(flagged)}   "
          + ", ".join(f"{k}={sum(1 for t in flagged if k in t.feedback)}"
                      for k in ("misheard", "wrong_entity", "wrong_class")))

    runs = failure_runs(turns)
    multi = [r for r in runs if len(r) > 1]
    print(f"\nfailure runs: {len(runs)} ({len(multi)} with more than one attempt)")
    for run in runs:
        head = run[0]
        mark = "  [human: misheard]" if any(t.misheard for t in run) else ""
        print(f"  x{len(run)}  {head.heard[:58]!r}{mark}")
        for t in run[1:]:
            print(f"       then {t.heard[:54]!r}")

    props = rephrases(turns)
    print(f"\nrephrase proposals: {len(props)}   "
          f"({sum(1 for p in props if p.human_confirmed)} human-confirmed)")
    for p in sorted(props, key=lambda p: (not p.human_confirmed, -p.frame_similarity)):
        extra = (f"  [from {p.surface_all!r}]"
                 if p.surface_all != p.surface else "")
        print(f"  [{p.strength:15}] {p.surface!r} -> {p.entity}"
              f"   (sim {p.frame_similarity}, {p.gap_s:.0f}s){extra}")
        print(f"       {p.failed.heard[:60]!r}")
        print(f"    -> {p.resolved.heard[:60]!r}")

    # Utterances a human flagged as misheard that no rephrase explains. They are the
    # honest residue: something was said, it was wrong, and nothing in the session says
    # what it should have been. `expected: null`, exactly as the design requires.
    explained = {p.failed.uid for p in props}
    unexplained = [t for t in turns if t.misheard and t.uid not in explained]
    if unexplained:
        print(f"\nmisheard with no rephrase to resolve them: {len(unexplained)}")
        for t in unexplained:
            print(f"  {t.heard[:66]!r}")
        print("  (expected: null - a run of failures does not say what was meant)")

    if not args.write:
        print("\n(pass --write to emit analysis.json for harvest_aliases.py)")
        return

    out = chosen.parent / "analysis.json"
    out.write_text(json.dumps({
        "session": chosen.parent.name,
        "source": "gameplay",
        "note": "Rephrase pairs are PROPOSALS. Frame similarity does not separate real "
                "pairs from false ones on this data - a false pair scores 0.76 and a "
                "real one 0.72 - so nothing here is a label until a human accepts it. "
                "`human_confirmed` means a misheard button was pressed on the failed "
                "utterance, which is the only ground truth in the file.",
        "utterances": len(turns),
        "records": [
            # The shape harvest_aliases.py consumes: a transcript and what it should have
            # resolved to. `source` travels with it so organic data stays measurable
            # apart from the scripted set, as the capture design requires.
            {"id": f"{chosen.parent.name}:{p.failed.uid}",
             "boosted_text": p.failed.heard,
             "expected": [p.entity],
             "surface": p.surface,
             "surface_all": p.surface_all,
             "source": "gameplay",
             "evidence": p.strength,
             "frame_similarity": p.frame_similarity,
             "gap_s": round(p.gap_s)}
            for p in props],
        "unresolved": [
            {"id": f"{chosen.parent.name}:{t.uid}", "boosted_text": t.heard,
             "expected": None, "source": "gameplay", "evidence": "human: misheard"}
            for t in unexplained],
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
