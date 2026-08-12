"""Label the prompt set by query class, and add a batch for the six with no coverage.

`score_router.py` scores **entity resolution only**: `expect_entities` is a set of names,
and a no-entity prompt is correct when the router names nothing. That was a reasonable
measurement of a five-class router where four classes named something.

It is not a measurement of a twelve-class one. **Six of the current classes name no
entity at all** - `tech_next`, `base_site`, `base_rating`, `base_criteria`,
`general_knowledge` and `pal_search` - so on that scorer they are indistinguishable from
each other and from an honest decline. The headline 88.8% says nothing about whether the
router picks `base_rating` over `general_knowledge`, and nothing ever has.

This script closes both halves of that:

1. **Labels the existing 1,031 prompts** with `expect_branch`, by matching each one back
   to the template that generated it. Nothing is rewritten - a prompt keeps its id and
   its text, so recordings already on disk stay valid - and a template whose class is
   genuinely ambiguous is labelled `None` and stays unscored rather than guessed at.
2. **Appends a generated batch** covering the six, from new templates.

## Why a separate batch rather than a wider COMPOSITION

`extend_stt_prompts.py` says of its composition: *"Keep this stable: changing it
mid-collection makes early and late batches non-comparable."* Adding slices to it would
do exactly that to 24 recorded batches. So this follows `add_branch_batch.py`'s
precedent - its own batch number and its own id prefix, sorting ahead of the unrecorded
remainder so the next session captures it.

## What "unsupported" means, and why it is worth labelling

A good third of the existing templates ask for things this product does not do: breeding
combos, stamina numbers, whether a Pal is worth levelling. Labelling those `unsupported`
rather than leaving them blank turns a blind spot into a measurement - it separates *the
router chose the wrong class* from *the router correctly declined something we cannot
answer*, which the entity-only scorer has always conflated.

Usage: python tools/eval/add_class_batch.py            # label and append
       python tools/eval/add_class_batch.py --dry-run  # report only, write nothing
       python tools/eval/add_class_batch.py --label-only
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PROMPTS = REPO / "data" / "stt_eval" / "prompts.json"

BATCH = -2
SEED = 20260812
# 36, one sitting, six per class. Small on purpose: these are unrecorded prompts and the
# point is coverage of twelve classes rather than depth in six.
PER_CLASS = 6

# Every template in extend_stt_prompts.py and build_stt_prompts.py, with the class it
# asks for. `None` means genuinely ambiguous and stays UNSCORED - guessing here would
# manufacture a wrong answer for the router to be marked against.
#
# `unsupported` means the product has no class for it. That is not a gap in the labelling,
# it is a fact about the corpus: it was written for assumption A5, which measured entity
# recognition and did not care whether the question was answerable.
TEMPLATE_CLASS: dict[str, str | None] = {
    # --- Pal templates
    "where can I find {pal}": "pal_location",
    "where do {pal} spawn": "pal_location",
    "where's the nearest {pal}": "pal_location",
    "where do {pal} spawn at night": "pal_location",
    "show me where {pal} are right now": "pal_location",
    "help me find {pal} around here": "pal_location",
    "can you find me a {pal} somewhere close": "pal_location",
    "find me a {pal} near my second base": "pal_location",
    "show me {pal} near my base": "pal_location",
    "how do I get {pal}": None,          # location or breeding, and the roadmap says so
    "what does {pal} drop": "pal_drops",
    "what do I get from {pal} and {pal2}": "pal_drops",
    "is {pal} good for mining": "pal_info",
    "is {pal} any good for logging": "pal_info",
    "what element is {pal}": "pal_info",
    "what work suitability does {pal} have": "pal_info",
    "can {pal} carry things back to base": "pal_info",
    "how rare is {pal}": "pal_info",
    "is {pal} better than the normal one": "pal_info",
    "is {pal} worth catching": None,     # info, or a judgement we do not make
    "does {pal} work at night": None,    # a spawn filter or a work question
    # --- unsupported: real questions, no class
    "how do I breed {pal}": "unsupported",
    "what's the breeding combo for {pal}": "unsupported",
    "can I breed {pal} with {pal2}": "unsupported",
    "do {pal} and {pal2} make anything good": "unsupported",
    "is {pal} worth levelling up": "unsupported",
    "how much stamina does {pal} have": "unsupported",
    "what's a good partner skill for {pal}": "unsupported",
    "what level should {pal} be before the tower": "unsupported",
    "is {pal} better than {pal2} for handiwork": "unsupported",
    "which should I use {pal} or {pal2}": "unsupported",
    "compare {pal} and {pal2} for combat": "unsupported",
    "should I put {pal} or {pal2} on the ranch": "unsupported",
    "is {pal} faster than {pal2}": "unsupported",
    "what's the best way to catch {pal}": "unsupported",
    "do I need a better sphere for {pal}": "unsupported",
    "is it worth going after {pal} tonight": "unsupported",
    "can I get {pal} before the first tower": "unsupported",
    # The named entity is the ATTACKER, not the target. `_COUNTER_CUES` leaves these to
    # the model deliberately, and labelling it boss_counter would score the abstention as
    # a failure.
    "should I use {pal} against the first tower": None,
    "is {pal} any good against the first tower": None,
    # --- resources
    "where's the nearest {res}": "resource_location",
    "where's the closest {res} deposit": "resource_location",
    "show me {res} near my base": "resource_location",
    "is there any {res} around here": "resource_location",
    "where do I mine {res}": "resource_location",
    "what's the best {res} node nearby": "resource_location",
    "how far is the nearest {res}": "resource_location",
    "can I get {res} at this level": "resource_location",
    "show me a safe {res} spot": "resource_location",
    "is there {res} near the first tower": "resource_location",
    "what's the closest place to farm {res}": "resource_location",
    "point me at some {res}": "resource_location",
    "any {res} worth mining nearby": "resource_location",
    "find me a {res} spot for level twenty": "resource_location",
    "I need {res} for a new base": "resource_location",
    "where should I set up for {res}": "base_site",     # a base FOR a resource
    "do I have enough {res} for this": "unsupported",   # inventory; nothing reads it
}

# The no-entity frames crossed with their topics. Each frame is a question SHAPE and the
# topic decides the class, so these are labelled by topic rather than by frame - which is
# also what makes them a real test: the frame is identical across classes.
TOPIC_CLASS: dict[str, str | None] = {
    "my next research": "tech_next",
    "technology points": "tech_next",
    "my second base": "base_site",
    "a second breeding pen": "unsupported",
    "the artisan trait": "general_knowledge",
    "the lucky trait": "general_knowledge",
    "sanity in the base": "general_knowledge",
    "pals getting depressed": "general_knowledge",
    "hot and cold weather": "general_knowledge",
    "raid attacks": "general_knowledge",
    "fast travel points": "general_knowledge",
    "the pal box limit": "general_knowledge",
    "cooking food": "general_knowledge",
    "my capture rate": "general_knowledge",
    "the worker limit": "general_knowledge",
    "the first tower": None,             # a fight, a location, or the gate on a tech
    "my stat points": "unsupported",
    "levelling up faster": "unsupported",
    "upgrading my gear": "unsupported",
    "the next tier of gear": "unsupported",
    "my weapon choice": "unsupported",
    "feeding overnight": "unsupported",
    "base defence": "unsupported",
    "my guild setup": "unsupported",
}

# --------------------------------------------------------------- the new batch
#
# Six classes with no coverage at all. Templates rather than hand-written sentences, so
# each class gets several phrasings and the set can grow the same way the rest did.
#
# **These are author-written and that is the known weakness.** They are my guesses at how
# somebody asks, which measures the plumbing more than the router - the same caveat
# score_corpus.py carries. Real phrasings from a play session should replace or extend
# them, and `Docs/test-plan.md` is where those will come from.
CLASS_TEMPLATES: dict[str, list[str]] = {
    "tech_next": [
        "hey pal what should I research next",
        "hey pal what can I unlock at level {level}",
        "hey pal what should I spend my ancient points on",
        "hey pal what weapon should I research next",
        "hey pal what should I research for my base",
        "hey pal what's worth unlocking right now",
        "hey pal which technology should I get next",
        "hey pal what should I prioritise in the tech tree",
    ],
    "base_site": [
        "hey pal where should I build my base for {res}",
        "hey pal where should I put a base for {res}",
        "hey pal best base spot for {res}",
        "hey pal where's a good place for a base with {res}",
        "hey pal where should I set up a {res} base",
        "hey pal find me a base site near {res}",
    ],
    "base_rating": [
        "hey pal how good is my base location",
        "hey pal rate this base location",
        "hey pal is this a good spot for a base",
        "hey pal how good is this base spot",
        "hey pal is this a good spot for a {res} base",
        "hey pal how good is my base location for {res}",
        "hey pal what do you think of this base location",
        "hey pal rate my base",
    ],
    "base_criteria": [
        "hey pal what makes a good base",
        "hey pal what makes a good base location",
        "hey pal what should I look for in a base location",
        "hey pal how do I choose a base location",
        "hey pal what do you check for a base spot",
        "hey pal what matters when picking a base site",
    ],
    "general_knowledge": [
        "hey pal how does sanity work",
        "hey pal what is item rot",
        "hey pal how do elements work",
        "hey pal what are pal effigies",
        "hey pal how does the breeding farm work",
        "hey pal what are predator pals",
        "hey pal explain ancient technology",
        "hey pal how does fishing work",
    ],
    "pal_search": [
        "hey pal I need a mining pal",
        "hey pal what {element} pals are around level {level}",
        "hey pal which pals can ranch",
        "hey pal what pal is best at mining",
        "hey pal give me a {element} pal at level {level}",
        "hey pal what's the fastest mount I can get at level {level}",
        "hey pal which mounts don't I have yet",
        "hey pal I need a new logging pal",
    ],
}
LEVELS = ["twenty", "thirty", "forty", "fifty", "sixty"]
ELEMENTS = ["electric", "fire", "water", "ice", "grass", "dragon"]


def _pattern(template: str) -> re.Pattern:
    """A template turned into a matcher for the sentences it generated.

    Reconstructed rather than recorded, because the generator never stored which template
    produced which prompt. `natural()` rewrites "a ore" to "an ore" after formatting, so
    the article is matched loosely.
    """
    parts = re.split(r"(\{\w+\})", template)
    out = []
    for part in parts:
        if part.startswith("{"):
            out.append(r".+?")
        else:
            escaped = re.escape(part)
            # "a {res}" becomes "an ore" downstream.
            escaped = escaped.replace(r"\ a\ ", r"\ an?\ ")
            out.append(escaped)
    return re.compile(r"^(?:hey pal )?" + "".join(out) + r"$", re.I)


def label(prompts: list[dict], resources: set[str]) -> Counter:
    """Attach `expect_branch` where a template match is unambiguous."""
    patterns = [(_pattern(t), cls) for t, cls in TEMPLATE_CLASS.items()]
    topics = sorted(TOPIC_CLASS, key=len, reverse=True)
    stats: Counter = Counter()

    for p in prompts:
        if "expect_branch" in p:
            stats["already labelled"] += 1     # the branch batch
            continue
        if p.get("group") == "control":
            # The four bare names. They are a diagnostic for the audio pipeline and not
            # a question at all, so there is no class for them to have.
            stats["control (no class)"] += 1
            continue
        text = p["text"]

        if p.get("difficulty") == "no_entity":
            topic = next((t for t in topics if t in text.lower()), None)
            cls = TOPIC_CLASS.get(topic) if topic else None
            if topic is None:
                stats["no_entity: topic unmatched"] += 1
            elif cls is None:
                stats["no_entity: deliberately unscored"] += 1
            else:
                p["expect_branch"] = cls
                stats[f"labelled {cls}"] += 1
            continue

        matched = [cls for pat, cls in patterns if pat.match(text)]
        if len(set(matched)) > 1:
            # "Where's the nearest X" is generated from a Pal template AND a resource
            # one, so 46 prompts matched both. **The prompt already says which** - the
            # entity it expects is either a resource or it is not - so this is read from
            # the data rather than resolved by taking the first match, which would have
            # been a coin flip dressed as a rule.
            kinds = {"resource_location" if e in resources else "pal_location"
                     for e in p.get("expect_entities", [])}
            if len(kinds) == 1 and kinds <= set(matched):
                matched = list(kinds)
            else:
                stats["ambiguous match"] += 1
                continue

        if not matched:
            stats["template unmatched"] += 1
        elif matched[0] is None:
            stats["deliberately unscored"] += 1
        else:
            p["expect_branch"] = matched[0]
            stats[f"labelled {matched[0]}"] += 1
    return stats


def generate(rng: random.Random, resources: list[str]) -> list[dict]:
    """`PER_CLASS` prompts for each of the six, cycling templates for variety."""
    out = []
    for cls, templates in CLASS_TEMPLATES.items():
        chosen = list(templates)
        rng.shuffle(chosen)
        for i in range(PER_CLASS):
            t = chosen[i % len(chosen)]
            text = t.format(res=rng.choice(resources).replace("_", " "),
                            level=rng.choice(LEVELS),
                            element=rng.choice(ELEMENTS))
            # `base_site` and `base_rating` name a resource, so the corrector CAN rank it
            # and the entity axis should expect it. Everything else names nothing.
            entities = []
            if "{res}" in t:
                res = re.search(r"for (?:a )?([a-z ]+?)(?: base)?$", text)
                entities = [m.replace(" ", "_") for m in ([res.group(1)] if res else [])]
            out.append({
                "group": "utterance", "text": text,
                "expect_entities": [e for e in entities if e in resources],
                "expect_branch": cls,
                "difficulty": "class_coverage",
                "batch": BATCH,
            })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--label-only", action="store_true")
    args = ap.parse_args()

    import sys
    sys.path.insert(0, str(REPO))
    from palintel.knowledge import KnowledgeBase
    resources = sorted({n.resource for n in KnowledgeBase.load("1.0.2").nodes})

    doc = json.loads(PROMPTS.read_text(encoding="utf-8"))
    prompts = doc["prompts"]

    stats = label(prompts, set(resources))
    print("labelling the existing set:")
    for k, v in sorted(stats.items(), key=lambda kv: -kv[1]):
        print(f"  {v:5}  {k}")
    labelled = sum(1 for p in prompts if p.get("expect_branch"))
    print(f"  ----- {labelled} of {len(prompts)} now carry expect_branch "
          f"({labelled / len(prompts):.0%})")

    new: list[dict] = []
    if not args.label_only:
        new = generate(random.Random(SEED), resources)
        start = max(int(re.sub(r"\D", "", p["id"]) or 0) for p in prompts)
        for i, p in enumerate(new):
            p["id"] = f"C{i + 1:02d}"
        print(f"\nnew class-coverage batch ({BATCH}): {len(new)} prompts")
        for cls in CLASS_TEMPLATES:
            print(f"  {cls:20} {sum(1 for p in new if p['expect_branch'] == cls)}")

    covered = Counter(p["expect_branch"] for p in prompts + new
                      if p.get("expect_branch"))
    print("\nclass coverage across the whole set:")
    for cls, n in covered.most_common():
        print(f"  {n:5}  {cls}")

    if args.dry_run:
        print("\n(dry run - nothing written)")
        return

    doc["prompts"] = prompts + new
    doc["count"] = len(doc["prompts"])
    doc["class_labelled"] = sum(1 for p in doc["prompts"] if p.get("expect_branch"))
    doc["class_note"] = (
        "expect_branch is the query class the router should choose. Added 2026-08-12, "
        "because score_router.py measures ENTITY resolution and six of the twelve "
        "production classes name no entity - so on that scorer they are "
        "indistinguishable from each other and from an honest decline. Prompts whose "
        "class is genuinely ambiguous carry no expect_branch and stay unscored on that "
        "axis; 'unsupported' means the product has no class for the question, which "
        "separates a wrong class from a correct decline.")
    PROMPTS.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(f"\n-> {PROMPTS}")


if __name__ == "__main__":
    main()
