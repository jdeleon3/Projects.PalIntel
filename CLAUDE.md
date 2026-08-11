# PalIntel — working instructions

## Start every session by catching up

Before doing anything else in this repository, read:

1. **[`STATUS.md`](STATUS.md)** — where the project is, what is measured, what is only
   *apparently* measured, and what is waiting on a decision.
2. **[`Docs/04-roadmap.md`](Docs/04-roadmap.md)** — the record of how each number was
   arrived at, including the measurements that were wrong and why.

This is not ceremony. The roadmap is the project's memory of **measurements that changed
a decision**, and several of them reversed conclusions that looked obvious first time —
a threshold that was discarding correctly-ranked candidates, a rule that scored 8/29 when
it looked derivable from five examples, a "regression" that was a depleted API balance.
Acting on a stale reading of this project is the most likely way to be confidently wrong
in it.

Then check `git log --oneline -10`, since STATUS.md is hand-maintained and may trail the
last session.

## Keep STATUS.md current

Update it when something lands that changes the answer to *"where is this project?"* — a
phase closing, a class shipping, a measurement resolving or a gap opening. It is the file
a future session reads first, so a stale one is worse than none.

## What this project is careful about

Read [`Docs/adr/README.md`](Docs/adr/README.md) before proposing anything that
reintroduces a discarded approach; several ADRs exist specifically to record why an
obvious idea was rejected.

The invariant underneath all of them: **coordinates, stats and breeding pairs never
originate from a model.** Tier 1 and Tier 2 factual values reach the card from typed
results without passing through a generative model. A card that is confidently wrong is
the one failure this project refuses to ship — being unable to answer is always
preferable to answering incorrectly.

Two habits that follow from it, and that repeated mistakes in this repo have earned:

- **Well-formed and wrong is the failure mode here.** Bad data in this project does not
  look bad: dungeon coordinates are in-bounds and correctly transformed, a level-80 drop
  table parses cleanly, Japanese item names are valid strings. Verify against something
  independent rather than checking that the output looks reasonable.
- **A derived rule is a claim.** If a mapping is inferred rather than stated by the data
  — a `BOSS_` prefix meaning "the alpha of", a category standing in for a drop — say so
  where it is published, and measure how much depends on it before trusting it.

## Practical notes

- Eval runs cost real money against a prepaid Gemini balance. Use
  `score_router.py --sample 60` (~$0.22) as a pre-flight; keep full runs (~$1.40) for
  confirming a decision, and write the decision rule down before running.
- `data/` is gitignored and regenerated from the local game install; see
  [`Docs/03-data-ingestion.md`](Docs/03-data-ingestion.md) §8 for the refresh commands.
- Comment density here is high and deliberate: comments explain *why*, especially where a
  measurement contradicted the obvious choice. Match it.
