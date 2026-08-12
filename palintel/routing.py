"""Intent routing — utterance to typed tool call.

The router owns two decisions the rest of the system cannot make:

  1. which tool (if any) the utterance means
  2. which entity a mangled transcript fragment refers to

The second used to live in the corrector, which had only a string to go on. The router
has sentence context - "against the first tower" implies a combat matchup, "how do I
breed X" constrains X to a species - and selects from a constrained enum, so it makes a
forced choice rather than a threshold judgement.
See Docs/adr/0016-entity-resolution-in-router.md.

The backend is swappable. A deterministic stub lets the whole downstream pipeline be
built and tested before a model is chosen; the model slots in behind the same protocol.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Protocol

from .knowledge import Candidate, Lexicon
from .tools import Decline, ToolCall

log = logging.getLogger("palintel.routing")

# How many candidates the corrector hands the router. Measured on 67 entity-bearing
# utterances (batches 0-1): recall@10 = 94.0%, @15 = 95.5%, and flat from there until
# @100. Gorirat sat at rank 11 - one past the old cutoff. Past 15 the extra candidates
# are noise the router has to reject, so this is the knee, not a maximum.
CANDIDATE_LIMIT = 15

# The routing policy every backend shares. Only the output-format sentence differs per
# backend (a tool call for Claude and Gemini, a JSON object for the local grammar), so
# the judgment rules live here and are worded once. They were previously duplicated in
# routing_anthropic.py and routing_local.py and had already drifted apart, which meant
# the hosted models were being compared on different instructions.
ROUTING_POLICY = """\
Speech-to-text mangles Palworld proper nouns, so the utterance may contain a corrupted \
entity name. A ranked list of candidate entities is supplied with each query, produced \
by phonetic and edit-distance matching. Treat it as a hint, not an answer - it has no \
sentence context and you do. Use the phrasing to judge which candidate the speaker \
meant: "where's the nearest X" implies a resource or location, "how do I breed X" \
implies a Pal. The list is not exhaustive; if the phrasing clearly names an entity that \
is not in it, you may still name that entity.

Resolve the entity whenever the phrasing and the candidates agree on a clear best \
reading, even when the transcript is badly mangled - "what does Vanwyrms drop" means \
Vanwyrm, "what level should Shroomr be" means Shroomer, "the breeding combo for Gizmos" \
means Gumoss. A plural, a dropped syllable, or a misheard vowel is not ambiguity.

Decline when two or more candidates are genuinely plausible for the same slot and \
nothing in the sentence separates them, or when no candidate fits the phrasing at all.

Both failures are real. A card that confidently answers the wrong question is worse \
than one that admits the miss, because the player acts on it mid-game and cannot tell \
it was wrong. But declining a query you could have answered is also a failure, and on \
measured data it is much the more common one.

Name exactly the entities the query is about - one for a question about a single Pal, \
two only when the query genuinely names two. Never list variants, alternatives, or \
runners-up. Naming two Pals when the speaker meant one is a wrong answer, not a hedge: \
the answer is a card, and a card cannot ask which one you meant.\
"""


# How the shared conversation context is worded for every backend. One function rather
# than three, for the reason ROUTING_POLICY is one string: the backends were once given
# subtly different instructions and compared as though they were not.
CONTEXT_POLICY = """\
Earlier turns from this speaker are listed below, oldest first. Use them ONLY to resolve \
a reference the current utterance cannot resolve on its own - "what about the alpha", \
"where's the closest one", "and coal?". If the current utterance names its own entity and \
reads as a fresh question, ignore the history entirely: a stale referent produces a card \
that looks authoritative and answers a question nobody asked.\
"""


def context_block(context: "list | None") -> str:
    """Render recent turns for a model prompt, or empty string when there are none."""
    if not context:
        return ""
    lines = "\n".join(f"  {i}. {turn}" for i, turn in enumerate(context, 1))
    return f"{CONTEXT_POLICY}\n\nEarlier turns:\n{lines}\n\n"


class RouterBackend(Protocol):
    """Anything that can turn an utterance into a tool call or an honest decline."""

    name: str

    def route(self, utterance: str, candidates: list[Candidate],
              context: list | None = None) -> ToolCall | Decline:
        ...


class FastPathRouter:
    """Answer deterministically when the phrasing is unambiguous; otherwise ask the model.

    The model is a ~2s network round trip and Q1's whole budget is 2.5s end to end, so on
    a plain "where's the nearest coal" the round trip is the entire latency problem. The
    stub answers that in microseconds from the same knowledge base and the same lexicon,
    with no model in the loop to fabricate anything.

    This is safe only because the stub declines rather than guesses. Measured on the A5
    transcripts it answered 12 of the 15 Q1 prompts, every one with the right resource,
    and claimed nothing belonging to another query class - and where it did answer, the
    model had independently made the same call. It defers everything else, including
    "do I have enough sulfur for this", which names a resource but is not a location
    question at all, and which stayed deferred through every cue widening.

    The order matters and is not reversible: the stub goes first because a `ToolCall`
    from it is a certainty, not a preference. If it ever became a preference, this would
    be the class that quietly outvotes a better router.
    """

    def __init__(self, fast: RouterBackend, full: RouterBackend):
        self.fast = fast
        self.full = full
        self.name = f"{fast.name}->{full.name}"

    def __getattr__(self, item):
        # `last_usage` and friends belong to the model, which is where the cost is.
        return getattr(self.full, item)

    def route(self, utterance: str, candidates: list[Candidate],
              context: list | None = None) -> ToolCall | Decline:
        call = self.fast.route(utterance, candidates, context)
        if isinstance(call, ToolCall):
            log.info("fast path: %s(%s) - no model call", call.name, call.args)
            return call
        # A stub decline asking for restatement is a considered answer, not a miss: the
        # speaker referred back to something that has expired, and the model cannot see
        # further back than the stub just did. Asking it would spend a round trip to
        # reach the same conclusion, or worse, to invent a referent.
        if isinstance(call, Decline) and call.needs_restatement:
            return call
        return self.full.route(utterance, candidates, context)


class FallbackRouter:
    """A router with a deterministic backstop for when the hosted one does not answer.

    Only `transient` declines fall through - a timeout, a rate limit, a 5xx. A considered
    decline is the model's answer and is passed on untouched, because the stub has strictly
    less information than the model did and re-deciding on less is how a "no" becomes a
    confidently wrong "yes".

    The point is not to salvage latency. A request that timed out has already blown the
    2.5s budget several times over, and nothing recovers that. It is that the player gets
    a card rather than an apology where one can honestly be given.

    **The backstop must be a MORE PERMISSIVE stub than any fast path in front of it.** An
    identical one is dead code: `FastPathRouter` asks the stub first, so anything reaching
    the model is by definition something that stub already declined, and asking the same
    deterministic router the same question again returns the same decline. That was true
    of the first version of this class - it could not rescue a single query in the default
    configuration while its docstring claimed otherwise. `build_router` now hands it a
    stub with a lower resource floor, which is what makes the fallthrough mean anything.
    """

    def __init__(self, primary: RouterBackend, backstop: RouterBackend):
        self.primary = primary
        self.backstop = backstop
        self.name = f"{primary.name}+{backstop.name}"

    def __getattr__(self, item):
        # Callers reach past the protocol for `last_usage`, `delete_cache` and friends.
        # Forwarding keeps the wrapper invisible to them rather than making every call
        # site aware of it.
        return getattr(self.primary, item)

    def route(self, utterance: str, candidates: list[Candidate],
              context: list | None = None) -> ToolCall | Decline:
        call = self.primary.route(utterance, candidates, context)
        if isinstance(call, Decline) and call.transient:
            log.warning("%s did not answer (%s) - falling back to %s",
                        self.primary.name, call.reason, self.backstop.name)
            fallback = self.backstop.route(utterance, candidates, context)
            if isinstance(fallback, ToolCall):
                return ToolCall(name=fallback.name, args=fallback.args,
                                rationale=f"{call.reason}; {fallback.rationale}")
            # Both failed. The player is owed the reason they can act on - the router
            # being unreachable - not the stub's narrower complaint about vocabulary.
            return Decline(reason=call.reason, known_options=fallback.known_options,
                           transient=True)
        return call


# --------------------------------------------------------------------------- stub

# Phrasings that clearly ask for a location. Anything outside them declines rather than
# guessing, which is what keeps the stub honest about its own coverage.
#
# Two sets, because widening them is a measured trade rather than an obvious improvement.
# Scored on the 15 A5 prompts a Q1 build can actually answer, with precision checked
# across all 232 - the way a wider list fails is by claiming queries from OTHER classes,
# and those live outside Q1:
#
#   standard    8/15 = 53%   0 claimed outside Q1
#   proximity  10/15 = 67%   0 claimed outside Q1
#   wide       12/15 = 80%   0 claimed outside Q1
#
# Nothing was stolen at any width and no resource was ever wrong.
#
# Phase 2 registered `find_pal_spawns` and that prediction came true, on the entry it
# named. Re-measured over all 240 A5 transcripts with both tools live:
#
#   cue set     Q1 right   Q2 right   claimed outside both classes
#   standard      10/18      23/49                 0
#   proximity     12/18      23/49                 0
#   wide          14/18      23/49                 9   <- before the branch split
#
# All nine were the intent-guessing entries firing on a Pal name: "is Pierdon any good
# for logging" and "do I need a better spear for Mereth" became spawn cards. The fix is
# not to drop `wide` - its entries were each earned by a real resource query - but to
# stop applying them to the Pal branch, which never justified them. See `pal_cues` in
# StubRouter.__init__: with that split, `wide` keeps 14/18 and steals nothing.
# See router.cues in config.
_CUE_SETS = {
    "standard": r"where|nearest|closest|find|locate|show me|spot|deposit|node",
    "proximity": r"where|nearest|closest|find|locate|show me|spot|deposit|node"
                 r"|near|nearby|around here|round here",
    # Every entry past "any" came from reading real transcripts, and none of them would
    # have been guessed. "gimme some quartz" ranked quartz at 1.00 and still paid 1.9s
    # because the list knew "get me" and not the contraction; "can I get coal at this
    # level" and "what's the best place to farm quartz" both had clean entities and no
    # cue at all, and were the two slowest ANSWERED queries of a session. Spoken phrasing
    # is not written phrasing, and `/palintel recent` is the only way to find the gap.
    #
    # Candidates were measured, not assumed: "gather", "harvest", "stock up" and "pick
    # up" added no coverage over these and are deliberately absent rather than included
    # on the theory that more is better.
    "wide": r"where|nearest|closest|find|locate|show me|spot|deposit|node"
            r"|near|nearby|around here|round here|i need|get me|gimme|give me|any"
            r"|place|farm|mine|can i get|is there",
}
DEFAULT_CUES = "wide"
# "What does X drop" is as templated as "where can I find X", and the fast path not
# claiming it is why the latency bar fails: p95 is the 95th percentile, so the 2.5s budget
# needs under 5% of queries reaching the model, and every unclaimed class puts the tail
# there by construction.
#
# Deliberately narrow, and deliberately disjoint from the location cues - none of these
# words appears in _CUE_SETS, so the two branches cannot fight over one utterance.
_DROP_CUES = re.compile(r"\b(drop|drops|dropped|yield|yields|get from|give|gives)\b", re.I)
# Q5. Deliberately narrow: every word here has to be about *fighting* a named thing,
# because this branch decides a TIER, not just a tool. A location question answered as
# a drop question is the wrong fact; a location question answered as a counter plan is
# a fact request answered with advice, which ADR-0010 separates on purpose.
#
# **Only phrasings that put the named entity in the TARGET position.** "What's good
# against Anubis" and "is Prixter any good against the first tower" share the cue
# `good against` and mean opposite things - the first names the boss, the second names
# the Pal you would bring, against a boss it never names. The stub has no way to tell
# them apart, and measured over the A5 transcripts it claimed three of the second kind
# and would have produced a plan for fighting Prixter.
#
# So `good against`, `strong against` and `use against` are OUT, despite reading like
# the most natural counter phrasings, and the model keeps them. What is left are verbs
# that take the boss as their object: you beat Anubis, you do not beat with Anubis.
#
# Inflections are part of the cue, not an afterthought. `\bbeat\b` does not match
# "beats", and the branch missed "what beats Vanwyrm" - a plainer counter question than
# several it did claim - until the batch measured it.
_COUNTER_CUES = re.compile(
    r"\b(?:counter|beat|defeat|kill|fight)(?:s|es|ing|en)?\b"
    r"|\bweak(?:ness)? (?:to|against)\b"
    # "What's VICTOR'S weakness" - the possessive puts the named entity in target
    # position just as firmly as "beat X" does, which is the rule this set is built on.
    # Asked in play on 2026-08-11 and paid a model round trip for it. Bare "weakness" is
    # still out: "what's my weakness" names nothing to fight.
    r"|\b[\w]+'s\s+weak(?:ness)?\b"
    r"|\btakes? on\b", re.I)

# --------------------------------------------------------------- attribute search
#
# The vocabulary for the one class that names no entity. Spoken words on the left, the
# pak's own enum on the right - "Electric" and "Grass" are what the player says and
# `Electricity` and `Leaf` are what the tables call them, the same split `cards.
# ELEMENT_DISPLAY` already carries in the other direction.
#
# Deliberately short. Every synonym here is one more way for a Pal name to be read as a
# type, and the branch is guarded on naming NO entity, so a false positive costs a real
# question. "Frost" is absent for that reason and "rock" is absent because it is already
# a resource alias for stone.
_ELEMENT_WORDS = {
    "fire": "Fire", "flame": "Fire",
    "water": "Water", "aqua": "Water",
    "ice": "Ice", "icy": "Ice",
    "electric": "Electricity", "electricity": "Electricity", "lightning": "Electricity",
    "grass": "Leaf", "leaf": "Leaf", "plant": "Leaf",
    "ground": "Earth", "earth": "Earth",
    "dark": "Dark",
    "dragon": "Dragon",
    "normal": "Normal", "neutral": "Normal",
}

# Job words, same shape. The keys are what a player says; the values are the
# `WorkSuitability_*` enum. Bare "mine" is NOT here - "where can I go mining" is a
# location question, and the `pal` requirement below is what separates them.
_WORK_WORDS = {
    "mining": "Mining", "miner": "Mining",
    "watering": "Watering",
    "planting": "Seeding", "seeding": "Seeding", "sowing": "Seeding",
    "handiwork": "Handcraft", "handcraft": "Handcraft", "crafting": "Handcraft",
    "lumbering": "Deforest", "logging": "Deforest", "woodcutting": "Deforest",
    "transporting": "Transport", "hauling": "Transport", "carrying": "Transport",
    "gathering": "Collection", "collecting": "Collection",
    "kindling": "EmitFlame",
    "cooling": "Cool",
    "medicine": "ProductMedicine",
    # "ranch" as a bare verb came from play: *"which pal's can ranch?"* deferred to the
    # model on 2026-08-11 because only the -ing form was here.
    "ranching": "MonsterFarm", "farming": "MonsterFarm", "ranch": "MonsterFarm",
}

_ELEMENT_ALT = "|".join(sorted(_ELEMENT_WORDS, key=len, reverse=True))
_WORK_ALT = "|".join(sorted(_WORK_WORDS, key=len, reverse=True))

# "an electric pal", "electric pals", "electric type". The type noun is REQUIRED: bare
# "fire" appears in questions about kindling, weapons and cooking, and only "fire Pal"
# is unambiguously about typing.
_ELEMENT_CUE = re.compile(
    rf"\b({_ELEMENT_ALT})[- ]?(?:type\s+)?pals?\b|\b({_ELEMENT_ALT})[- ]type\b", re.I)
# "a mining pal", and the prepositional forms: "best at mining", "a pal for watering".
# The bare preposition is safe here only because the branch already requires the word
# "pal" outside the wake address and an utterance that names no entity - "go for mining"
# on its own reaches neither.
_WORK_CUE = re.compile(
    rf"\b({_WORK_ALT})\s+pals?\b"
    rf"|\b(?:at|for)\s+({_WORK_ALT})\b"
    # "which pals CAN ranch" - the job trails the subject instead of qualifying it.
    # Straight from play; the two patterns above both wanted it in front.
    rf"|\bpals?\b[^.?]{{0,20}}?\bcan\s+({_WORK_ALT})\b", re.I)
# Two ways to say "generating electricity", which no single word covers.
_POWER_CUE = re.compile(
    r"\b(?:generat\w*\s+(?:electricity|power)|power\s+generation"
    r"|electricity\s+generation)\b", re.I)
_OIL_CUE = re.compile(r"\b(?:oil\s+extraction|extract\w*\s+oil)\b", re.I)

# The wake word is address, not content, and this branch tests for the word "pal". Left
# in, "hey pal, where can I go mining" reads as a question about mining Pals - which is
# a location question answered with a roster. `knowledge.WAKE_WORDS` solves the same
# problem for entity ranking and cannot be reused here, because that strips the word
# everywhere and this branch needs it to still count when the player says it themselves.
_ADDRESS = re.compile(r"^\s*(?:hey|ok|okay)[,\s]+(?:pal|palintel)\b[,\s]*", re.I)
_PAL_NOUN = re.compile(r"\bpals?\b", re.I)

# NOT here: a bare element plural as the subject noun, "show me level 60 dragons". It was
# built and reverted the same day. The measurement was fine - the plural fires on 2 of
# 281 transcripts and both are genuine - but making it work needed an allowlist of ONE
# element, because `plants`, `grounds` and `flames` are ordinary English about other
# things and would have turned "which plants can I grow" into a Grass roster.
#
# A rule with a single hand-picked exception is not a rule, it is a special case wearing
# one, and the next person has to learn both. "<element> pal" and "<element> type"
# already cover every element uniformly; the bare plural defers to the model, which reads
# the sentence rather than a pattern.

# Mounts. A separate cue family from elements and jobs because it changes what "level"
# MEANS - a saddle is gated on the player's level, everything else here on the Pal's.
_MOUNT_CUE = re.compile(r"\b(?:mount|mounts|ride|rideable|ridable|mountable"
                        r"|saddle|saddles)\b", re.I)
# Which medium, when the player says. Absent means "either", which ranks a Pal by
# whichever of its two speeds is higher rather than silently assuming land.
#
# "flying" and "ground" both map to `land`, and that is the game's distinction rather
# than a shortcut: there is no flight-speed column, so a flyer's ridden speed IS the
# ground speed field. See tools/ingest/build_mounts.py for the seven flight signals that
# were measured and falsified before grouping them.
_MEDIUM_CUES = (
    ("water", re.compile(r"\b(?:swim\w*|water|aquatic|sea|ocean)\b", re.I)),
    ("land", re.compile(r"\b(?:fly\w*|flight|air|aerial|ground|land|walk\w*|run\w*)\b",
                        re.I)),
)
_MEDIUM_CUES_BY_NAME = dict(_MEDIUM_CUES)
# "which mounts do I not have yet", "what am I missing". This INVERTS the answer, so a
# false positive returns the exact complement of what was asked - but it is only ever
# consulted inside the mount branch, which has already established there is no named
# entity and a mount cue, so the pattern can afford to read naturally.
#
# The negation and the verb are allowed a couple of words apart: "do I not have" puts a
# pronoun between them, which an adjacent-tokens pattern missed.
_UNOWNED_CUE = re.compile(
    r"\b(?:do\s?n[o']?t|have\s?n[o']?t|not|never)\s+(?:\w+\s+){0,2}"
    r"(?:have|got|own|owned|caught|catch|get)\b"
    r"|\bmissing\b"
    r"|\byet\s+to\s+(?:catch|get|own)\b", re.I)

# "Tell me about X". **The most-asked shape in the first play session** - nine of
# forty-one utterances - and the product had no class for it, so seven were answered by
# the wrong one: a location card for "tell me about Shroomer", a Tier 2 counter plan for
# "who is Victor".
#
# Disjoint from every other cue family here on purpose. It must sit BEHIND counters and
# drops in `route`, because "what's Victor's weakness" and "what do I get from X" are
# more specific readings of the same polite openers, and this branch would otherwise
# claim them and answer a narrower question with a summary.
# **"What's X" is NOT on this list, and the first version of it was.** Measured
# immediately: a generic `what(?:'s| is)` opener took Q2 from 43 to 42 with one wrong
# card, took claims outside the scored classes from 12 to 28, and broke three counter
# prompts the branch batch had been passing. It swallowed *"what's the nearest memorist"*
# (a location question), four *"what's the breeding combo for X"*, *"what's a good
# partner skill for X"*, and - worst - *"what's strong against Lyleen"*, which
# `_COUNTER_CUES` deliberately leaves to the model because the named entity may be the
# attacker rather than the target.
#
# The lesson is the one the cue sets keep teaching: a question OPENER is not an intent.
# Every phrase below names what is being asked for, not merely that something is.
_INFO_CUES = re.compile(
    r"\btell me about\b|\bwhat can you tell me\b"
    r"|\bwho(?:'s| is)\b"
    r"|\binfo (?:on|about)\b|\bdescribe\b"
    r"|\bwhat level is\b|\bhow good is\b",
    re.I)

# "Can I ride X" - a yes/no about ONE named Pal, which mount search cannot answer because
# it returns lists. Requires a modal, so it cannot collide with "which dragons can I
# ride", where the subject is a description rather than a name and the entity guard sends
# it to mount search instead.
_RIDE_ONE_CUE = re.compile(
    r"\b(?:can|could)\s+(?:i|you|we)\s+rid[e]\b|\bis\s+\w+\s+rid(?:e?able)\b"
    r"|\brideable\b|\bridable\b", re.I)

# ------------------------------------------------------------------ Q6 progression
#
# "What should I research next". Structurally the safest branch in this file after
# attribute search, and for the same reason: it abstains whenever the utterance names an
# entity, so it cannot take a query any other class could answer.
#
# The vocabulary is disjoint from every other cue family here. No word below appears in
# `_CUE_SETS`, `_DROP_CUES`, `_COUNTER_CUES` or `_INFO_CUES`, so the branches cannot fight
# over an utterance and the order between them is a convenience rather than a tie-break.
#
# **"Unlock" is the risky one and it is kept anyway.** "How do I unlock Anubis" is not a
# technology question, and it is caught by the no-entity guard rather than by the pattern
# - which is the right place for it, because the pattern cannot know what follows the verb
# and the candidate list can. What is deliberately NOT here is bare "build" or "make":
# "what should I build next" is genuinely ambiguous between a base structure and a
# crafting recipe, and it also reads as a base-siting question.
_TECH_CUES = re.compile(
    r"\b(?:research|researching|unlock|unlocks|unlocking)\b"
    r"|\btech(?:nolog(?:y|ies))?\s*(?:tree|point|points)?\b"
    r"|\bancient\s+(?:technology\s+)?points?\b", re.I)

# **The topic is not the question.** Swept over the 271 A5 transcripts, the topic cue
# alone claimed five prompts and stole none - but two of the five were *"can you explain
# technology points?"* and *"what changes with technology points?"*, which are requests
# for an explanation, and this class answers them with a shopping list. A wrong-class
# answer is worse than a decline because it looks like an answer, which is the finding
# the first play session paid for and `_INFO_CUES` already records.
#
# So a claim needs both halves: the topic, and a frame that asks for a RECOMMENDATION.
# This is the same rule as "a question opener is not an intent", read the other way - the
# opener has to name what is wanted, and here what is wanted is advice about what to do
# next. The two explanatory prompts have no frame and go to the model, which can read
# them; the three that survive are all answerable by the card.
_TECH_ASK = re.compile(
    r"\bshould i\b|\bwhat (?:can|could) i\b|\bwhat to\b|\bwhat's next\b"
    r"|\bnext\b|\bworth\b|\bspend\b|\brecommend\w*\b|\bpriorit\w*\b", re.I)

# Spoken goal words, mapped to the pak's own category. Deliberately short, and every entry
# is a word a player would actually say - the model path carries the full twelve-value
# enum, so nothing is lost by leaving `CaptureItemModifier` and `Consume` off a keyword
# list nobody would trigger.
_TECH_GOAL_WORDS = {
    "weapon": "Weapon", "weapons": "Weapon", "gun": "Weapon", "guns": "Weapon",
    "armor": "Armor", "armour": "Armor",
    "ammo": "Ammo", "ammunition": "Ammo", "bullets": "Ammo",
    "food": "Food", "cooking": "Food",
    "glider": "Glider",
    "accessory": "Accessory", "accessories": "Accessory", "ring": "Accessory",
    "sphere": "SpecialWeapon", "spheres": "SpecialWeapon",
    "saddle": "Essential", "saddles": "Essential",
    # The player's word for BuildObject is never "build object". These three are, and
    # "base" is the one they actually say.
    "base": "BuildObject", "building": "BuildObject", "structure": "BuildObject",
}
_TECH_GOAL_ALT = "|".join(sorted(_TECH_GOAL_WORDS, key=len, reverse=True))
_TECH_GOAL_CUE = re.compile(rf"\b({_TECH_GOAL_ALT})\b", re.I)

# "What should I spend my ancient technology points on" names a POOL, and the first
# version of this branch dropped that word and answered with ordinary technologies. Same
# failure as "which dragons can I ride at level 60" answering without the element: a
# filter the player stated, silently gone, on the fast path. The pak calls them boss
# technologies and the UI calls them Ancient Technology Points; players say the latter.
#
# There is no cue for the ordinary pool, deliberately - "technology points" is how people
# refer to the system as a whole, so reading it as a filter would narrow half the
# questions this branch exists for.
_ANCIENT_CUE = re.compile(r"\bancient\b", re.I)

# ------------------------------------------------------------------ Q7 corpus lookup
#
# "How does sanity work". The broadest branch here, and the last one consulted, because
# it is the only one whose subject is a MECHANIC rather than an entity or a description
# of one.
#
# Two guards, and it needs both:
#
# * **No named entity**, the same structural argument attribute search makes. "Tell me
#   about Shroomer" and "who is Victor" are pal_info's, and this branch must not take
#   them just because they are also explanatory in shape.
# * **It only claims what it can actually answer.** The branch calls the corpus and
#   defers when nothing clears the relevance floor, rather than claiming the utterance
#   and letting the dispatcher print "not in my sources". That distinction matters
#   because the fast path preempts the model: a decline this branch produces is a
#   question the model never got to try, and the corpus is the game's own text, so plenty
#   of reasonable Palworld questions are honestly outside it. Consulting it costs a
#   sub-millisecond scan over 3,106 chunks - the data model's own sizing note says exact
#   search is free at this size - so "claim only what you can answer" is affordable here
#   in a way it is not for any class behind a network call.
_EXPLAIN_CUES = re.compile(
    r"\bhow (?:does|do|is|are)\b"
    r"|\bwhat (?:is|are|does)\b"
    r"|\bexplain\b|\bwhat.{0,12}\bmean\b"
    r"|\bhow (?:do|can) (?:i|you)\b", re.I)


# ------------------------------------------------------------------ Q4 base siting
#
# "Where should I put a base for coal". Unlike the two branches above, this one NAMES
# entities - a base is built for something - so the no-entity guard is unavailable and
# the cue has to carry the whole distinction from an ordinary location question.
#
# **The distinction is a placement verb.** "Where's the coal near my base" and "where
# should I build my base for coal" both say `base` and both say `coal`, and only the
# second is asking where to put one. So the word `base` on its own claims nothing: it has
# to be the object of building, putting, placing, setting up, settling or starting. That
# also means no negative pattern is needed for "near my base" / "at my base" - those have
# no placement verb and never reach this branch.
_BASE_CUE = re.compile(
    r"\b(?:build|building|put|putting|place|placing|set\s*up|settle|start)\b"
    r"[^.?]{0,25}\bbase\b"
    r"|\bbase\s+(?:site|sites|spot|spots|location)\b"
    r"|\b(?:best|good)\s+(?:place|spot|location)\s+for\s+a\s+base\b", re.I)

# How many resources one base question may name. Three is the point at which a card stops
# being a recommendation and becomes a list of everything within reach of everything.
MAX_BASE_RESOURCES = 3

# "How good is my base location", "rate this spot". The mirror of the siting question and
# a different one: that searches for places, this judges a place the player is already
# standing on or has already built.
#
# It names no resource, which is what separates it from `_BASE_CUE` above without either
# pattern having to know about the other: "where should I build my base for coal" names
# coal and asks WHERE, this names nothing and asks HOW GOOD.
_RATE_CUE = re.compile(
    r"\bhow good\b|\brate\b|\bis this a good\b|\bany good\b|\bwhat do you think of\b"
    r"|\bhow (?:is|are)\b.{0,20}\b(?:base|spot|location)\b", re.I)
# The noun it has to be about, so "how good is Anubis" never reaches this branch - though
# the entity guard would catch that one anyway.
_RATE_SUBJECT = re.compile(r"\bbase\b|\bcamp\b|\bspot\b|\blocation\b|\bsite\b|\bhere\b",
                           re.I)
# Which of the two readings. "My base" is where they built; anything else is where they
# stand. Deliberately requires the possessive: "a base" and "the base" are not claims
# about ownership and default to the safer reading, which is the one that needs no save
# beyond a position.
_OWN_BASE = re.compile(r"\b(?:my|our)\s+(?:base|camp)s?\b", re.I)

_LOCATION_CUES = re.compile(rf"\b({_CUE_SETS[DEFAULT_CUES]})\b", re.I)
_LEVEL = re.compile(r"\b(?:level|lvl)\s*(\d{1,2})\b", re.I)
_LEVEL_WORDS = {
    "ten": 10, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
}

# Minimum similarity the stub will act on. A model-backed router does not need this -
# it weighs candidates against sentence context. The stub has no context, so it needs
# a floor, and the floor belongs here rather than back in the corrector: the corrector
# still hands every candidate to whichever router is in use.
MIN_CONFIDENT = 0.78

# The floor for the backstop only, where the alternative to answering is not a better
# answer but nothing at all - the model did not reply. That asymmetry justifies a lower
# bar than the fast path, which preempts a *working* model and must stay strict.
#
# It does not justify guessing, and the value is chosen by where wrong answers start
# rather than where coverage stops improving. Swept over the 232 A5 utterances with the
# Pal guard held at MIN_CONFIDENT:
#
#   resource floor   Q1 right   wrong   claimed outside Q1
#       0.78            12        0            0
#       0.68            12        0            0     <- this
#       0.64            13        0            3     "can I get Zendelord" -> ore
#       0.60            13        0            4     also answers a no-entity prompt
#
# 0.64 is where it starts putting confidently wrong cards on Pal queries, which is the
# failure ADR-0007 refuses to ship whether or not the model was reachable. 0.68 recovers
# two of the three mangled transcripts from a real session - "nearest goal" and "near a
# store" - and none of the wrong ones.
BACKSTOP_CONFIDENT = 0.68

# The floor for accepting WHICH Pal, which is a harder judgement than which resource and
# needs a higher bar. There are four resources and 313 Pals, so the ranker's top
# candidate for a mangled Pal name is far less trustworthy - the same asymmetry the STT
# hotword work found (19/19 resource clips clearing MIN_CONFIDENT against 42/60 Pal ones).
#
# Chosen the same way BACKSTOP_CONFIDENT was: by where wrong answers begin, not by where
# coverage stops improving. Swept over the 240 A5 transcripts at `proximity`:
#
#   pal floor   Q2 right   wrong   Q1 wrong
#      0.78        24        2         1     "Banner and Cryst" -> Rayhound Cryst
#      0.85        23        0         0     <- this
#      0.90        17        0         0
#      0.95        14        0         0
#
# 0.85 costs exactly one Q2 answer and removes every wrong card; above it coverage
# collapses for no correctness gain. The one it costs is not lost, only slower - it goes
# to the model, which has the sentence context to resolve it (ADR-0016).
PAL_CONFIDENT = 0.85

# Phrasings that refer back to an earlier turn rather than naming their own subject.
#
# Deliberately narrow. A false positive here answers a FRESH question with a STALE entity,
# which is the exact failure ADR-0013 names as the price of having memory at all - and it
# produces a card that looks entirely authoritative. Missing a follow-up only costs the
# speaker a restatement, so the asymmetry is not close.
#
# "one" and "it" are load-bearing and risky in equal measure: "where's the closest one"
# is unambiguously a follow-up, while "is it any good" is not a location question and
# never reaches this branch because the cue gate rejects it first.
_FOLLOWUP = re.compile(
    r"^\s*(what|how)\s+about\b"
    r"|^\s*and\b"
    r"|\b(that one|those|the same|the other one|the closest one|the nearest one)\b"
    r"|\b(it|one|them)\s*\??\s*$",
    re.I)


# Words that carry no subject of their own: the follow-up openers, and the modifiers a
# follow-up is allowed to add. Everything else left in an utterance is content, and
# content the router cannot place is a reason to defer rather than to inherit.
_OPENER_WORDS = frozenset({"about"})
_CONTRACTION_TAILS = frozenset({"s", "t", "re", "ve", "ll", "m", "d"})
_MODIFIER_WORDS = frozenset("""
alpha alphas lord lords predator predators boss night nighttime nocturnal dark
closest nearest close near nearby one ones other another same next else
spot spots place places area areas around here
""".split())


def _spoken_level(utterance: str) -> int | None:
    """A level out of an utterance, digits or the round words STT prefers.

    Shared by the resource branch, where it means the PLAYER's level, and the attribute
    branch, where it means the PAL's. That the same regex serves both is not an oversight
    - it is the inconsistency STATUS names when it records the decision: resource cards
    print `lvl 28+`, a player requirement, and a spawn card prints `lvl 68-72`, the Pal.
    The word already means two things in this product, and the 2026-08-11 decision only
    settled which one the new class uses. Extracting the NUMBER is the same job either
    way; what it means is the caller's to know.
    """
    if (m := _LEVEL.search(utterance)):
        return int(m.group(1))
    for word, value in _LEVEL_WORDS.items():
        if re.search(rf"\blevel\s+{word}\b", utterance, re.I):
            return value
    return None


def _residue(utterance: str, matched_text: str = "") -> set[str]:
    """Content words left after the opener, the function words and the named entity.

    This is what separates the three cases the inheritance rule has to tell apart:

      "and coal?"                  -> nothing left. Elliptical; inherit the verb.
      "what about the alpha?"      -> modifiers only. Inherit the entity too.
      "how about breeding Anubis"  -> "breeding" left. Its own verb; do not inherit.
      "and Banner and Cryst?"      -> "banner", "cryst" left. It names SOMETHING and we
                                      could not place it, so answering about the
                                      previous turn's coal would be a wrong card.
    """
    from .knowledge import STOPWORDS, WAKE_WORDS

    # Split contractions rather than keeping them whole. STOPWORDS holds "where", not
    # "where's", so tokenising with the apostrophe left "where's" looking like content
    # and turned every "where's the closest one" into a restatement request.
    words = set(re.findall(r"[a-z]+", utterance.lower()))
    words -= STOPWORDS | WAKE_WORDS | _OPENER_WORDS | _CONTRACTION_TAILS
    words -= set(re.findall(r"[a-z]+", matched_text.lower()))
    return words - _MODIFIER_WORDS


class StubRouter:
    """Deterministic keyword router. No model, no network.

    Used to build and test the pipeline before a model is chosen, as the transport
    backstop, and as the Q1 fast path.
    """

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None,
                 cues: str = DEFAULT_CUES, resource_floor: float = MIN_CONFIDENT,
                 pal_spawns: bool = True, pal_floor: float = PAL_CONFIDENT,
                 pal_drops: bool = True, counters: bool = False,
                 counterable: set[str] | None = None,
                 attributes: bool = True, info: bool = True,
                 progression: bool = False, base_sites: bool = False,
                 corpus: "Callable[[str], bool] | None" = None):
        """`resource_floor` is how well a resource must match to be answered on.

        Separate from the Pal guard, which stays at MIN_CONFIDENT, because one constant
        was doing two opposing jobs: it decided both "the top candidate is confidently a
        Pal, so this is a Pal question" and "this resource matched well enough to act
        on". Lowering it to be more permissive made the second looser and the first
        TIGHTER - a Pal at 0.71 started clearing the bar and triggering the guard - so a
        single-knob sweep from 0.78 to 0.55 recovered exactly one query out of 232 and
        looked like evidence that permissiveness does not help. It was evidence that the
        knob was wrong.

        `pal_spawns` turns a confident Pal match from a decline into a `find_pal_spawns`
        call. It is a switch rather than plain behaviour because Q1's "claimed nothing
        outside Q1" was measured when there was no other tool to claim *for*; turning it
        off restores exactly the Phase 1 router, which is the only way to attribute a
        regression to registering the second tool rather than to the cue width.
        """
        # Recognised and locatable are different sets. Crude oil is recognised - the
        # player can name it and deserves a real answer - but has no map locations, so
        # offering it as something we can "find" would be misleading.
        #
        # ORDER is preserved rather than sorted, because the caller knows which resources
        # matter and this class does not. With four resources alphabetical was fine; with
        # eighteen it opened every decline card with "ancient bark, ancient bone, ancient
        # lava" - three resources of seven clusters each that nobody has ever asked for.
        self._resources = set(lexicon.resources())
        # Kept whole, not just its resource names: the drops branch has to ask whether a
        # second confident candidate is a different Pal or the same one's variant, and
        # "Incineram Noct" ranks "Incineram" beside it at an identical score.
        self._lexicon = lexicon
        self._locatable = list(locatable) if locatable is not None \
            else sorted(self._resources)
        if cues not in _CUE_SETS:
            raise ValueError(f"unknown cue set {cues!r}, expected one of "
                             f"{sorted(_CUE_SETS)}")
        self._cues = re.compile(rf"\b({_CUE_SETS[cues]})\b", re.I)
        # The Pal branch is gated on the narrower set, never on `wide`, whatever the
        # resource branch is configured to. `wide`'s extra entries are intent guesses -
        # "any", "i need", "can i get" - and every one of them was added because a real
        # session showed it on a RESOURCE query. Applied to a Pal name they fire on
        # questions that are not about location at all: "is Pierdon any good for logging"
        # and "do I need a better spear for Mereth" both became spawn cards. Measured on
        # the A5 set, splitting the branches keeps `wide`'s Q1 coverage (14/18 against
        # proximity's 12) and takes queries claimed outside both classes from 7 to 0.
        pal_cues = "proximity" if cues == "wide" else cues
        self._pal_cues = re.compile(rf"\b({_CUE_SETS[pal_cues]})\b", re.I)
        self._floor = resource_floor
        self._pal_spawns = pal_spawns
        self._pal_drops = pal_drops
        self._pal_floor = pal_floor
        self._counters = counters
        # Which Pals have a boss form at all. Passed in rather than derived, because
        # `BOSS_<name>` meaning "the alpha of" is the derived rule CLAUDE.md flags, and
        # the router is the wrong place to re-infer it - bosses.json already did, and
        # recorded that it was an inference.
        self._counterable = {c.lower() for c in (counterable or ())}
        # Attribute search. On by default, unlike `counters`, because it needs no dataset
        # the router has to be handed and because its guard is structural rather than
        # tuned: the branch abstains whenever anything in the utterance resolves to a
        # named entity, so it cannot claim a query another class could answer.
        self._attributes = attributes
        # "Tell me about X". On by default for the same reason attribute search is: it
        # needs no dataset handed in, and its guard is the ordinary confident-Pal-plus-cue
        # gate the drop branch already uses.
        self._info = info
        # Q6. OFF by default and passed in by `build_router`, exactly like `counters` and
        # for the reason `pipeline._counterable` records at length: a branch that names a
        # tool whose dataset is absent produces a decline the player cannot act on. It is
        # gated on tech.json existing.
        #
        # And it is passed in rather than defaulted true because the omission that left
        # the counter fast path dark in production for a day was precisely a
        # `build_router` call that did not pass the flag. Making it explicit does not
        # prevent that; the test that asserts `build_router` turns it on does.
        self._progression = progression
        # Q4, gated the same way and for the same reason: base_camp.json carries the
        # radius, and a radius is the entire question this class asks.
        self._base_sites = base_sites
        # Q7. A CALLABLE rather than a flag, and that is the design: it answers "can the
        # corpus ground this?", so the branch claims only what it can actually answer
        # instead of preempting the model with a decline. None turns the branch off.
        self._corpus = corpus
        # Width, floor and registered classes are all in the name so they reach
        # `/palintel status` and every routing log line. A fast path that quietly widened,
        # or a backstop quietly answering on weaker matches, would be indistinguishable
        # from the model getting worse.
        self.name = (f"stub:{cues}"
                     + (f"@{resource_floor:g}" if resource_floor != MIN_CONFIDENT else "")
                     + (f"+pals@{pal_floor:g}" if pal_spawns else "")
                     + ("+attrs" if attributes else "")
                     + ("+info" if info else "")
                     + ("+tech" if progression else "")
                     + ("+bases" if base_sites else "")
                     + ("+corpus" if corpus is not None else ""))

    def _subject(self, candidates: list[Candidate]) -> tuple[str, str, str] | None:
        """(tool, slot, canonical) for the subject this utterance names, if any.

        Candidates arrive ranked, so the first to clear its own bar wins - and the bars
        differ by kind, which is the point: 4 resources against 313 Pals means a top Pal
        candidate is a much weaker signal than a top resource one.
        """
        for c in candidates:
            if c.kind == "resource" and c.canonical in self._resources \
                    and c.score >= self._floor:
                return "find_resource_nodes", "resource", c.canonical
            if c.kind == "pal" and self._pal_spawns and c.score >= self._pal_floor:
                return "find_pal_spawns", "pal", c.canonical
        return None

    def _counter_call(self, utterance: str, candidates: list) -> "ToolCall | None":
        """`plan_counters` when the utterance clearly asks how to FIGHT a named Pal.

        "Where can I find Anubis" and "how do I beat Anubis" resolve to the same lexicon
        entity, so the cue carries the whole distinction between a Tier 1 card and a
        Tier 2 one. Choosing wrongly does not return the wrong fact - it answers a fact
        request with advice, which is the worse of the two failures.

        **When both cue families fire, that is not ambiguity to resolve - it is two
        questions.** "Where can I find something to beat Anubis" wants a counter plan
        *and* a location, and picking one is a coin flip on the tier. So the spawn call
        is chained behind the counter call and both are answered. That is faster than
        deferring to the model and it cannot be wrong about the tier, because it does
        not choose one.

        It still abstains where abstaining is right: a Pal with no boss form cannot be
        fought as one, and deferring there rather than declining lets the model treat it
        as the different question it probably is.

        **A tower leader is the other kind of target.** "How do I beat Victor" names a
        human, not a species, and the lexicon carries the nine of them as their own
        entity kind precisely so the name survives to here - resolving Victor to
        Shadowbeak during ranking would hand the dispatcher a species that also has a
        field alpha, and the two are different fights.
        """
        if not (self._counters and candidates):
            return None
        if not _COUNTER_CUES.search(utterance):
            return None
        top = candidates[0]
        if top.score < self._pal_floor:
            return None
        if top.kind == "pal":
            if top.canonical.lower() not in self._counterable:
                return None
        elif top.kind != "leader":
            # A resource, or a kind added later. Either way not something to fight.
            return None

        # `self._cues` is the *wide* set on purpose: the question is whether any hint of
        # a location question is present, not whether the narrow gate would have claimed
        # it. Chained only when the spawn tool is actually registered - otherwise this
        # would name a tool the dispatcher does not have.
        #
        # Never chained for a leader. `find_pal_spawns` takes a species and there is no
        # Victor in the spawn table, so "where do I find Victor to beat him" would ask a
        # question the dataset cannot answer and get a decline card beside a good one.
        also_a_location = (bool(self._cues.search(utterance)) and self._pal_spawns
                           and top.kind == "pal")
        chained = (ToolCall(name="find_pal_spawns", args={"pal": top.canonical},
                            rationale="location cue alongside a counter cue")
                   if also_a_location else None)
        return ToolCall(name="plan_counters", args={"boss": top.canonical},
                        rationale=f"counter cue + boss-capable {top.kind} {top}",
                        then=chained)

    def _drops_call(self, utterance: str, candidates: list) -> "ToolCall | None":
        """`find_pal_drops` when the utterance clearly asks what a Pal yields.

        Gated the same way the spawn branch is - a confident Pal plus a cue - because the
        fast path preempts the model and anything it claims wrongly is a wrong card the
        model never got to prevent.

        The second-entity guard defers to the model when the utterance names two
        DIFFERENT Pals, since this tool has one slot and "what do I get from Astralym and
        Mycora" is two answers. A variant of the same Pal is not a second entity:
        "Incineram Noct" ranks Incineram alongside it at the same score, and the
        dispatcher renders the family anyway.
        """
        if not (self._pal_drops and candidates):
            return None
        top = candidates[0]
        if top.kind != "pal" or top.score < self._pal_floor:
            return None
        if not _DROP_CUES.search(utterance):
            return None
        second = candidates[1] if len(candidates) > 1 else None
        if (second and second.kind == "pal" and second.score >= self._pal_floor
                and not self._lexicon.same_family(top.canonical, second.canonical)):
            return None
        return ToolCall(name="find_pal_drops", args={"pal": top.canonical},
                        rationale=f"drop cue + pal candidate {top}")

    def _info_call(self, utterance: str, candidates: list) -> "ToolCall | None":
        """`get_pal_info` when the utterance asks what a named Pal *is*.

        Gated exactly like the drop branch - a confident Pal plus a cue - because the
        fast path preempts the model and anything it claims wrongly is a wrong card the
        model never got to prevent.

        **Pal kind only.** A tower leader tops the candidate list at 1.00 on "who is
        Victor", and there is no info card for a human: what the datasets hold is the
        Pal she fights with, and answering a question about a person with a Pal's stat
        line is the wrong-class failure this branch exists to remove, not repeat it.
        Those defer to the model.
        """
        if not (self._info and candidates):
            return None
        top = candidates[0]
        if top.kind != "pal" or top.score < self._pal_floor:
            return None
        # "Can I ride X" is an info question about a NAMED Pal, which is this branch's
        # shape - not mount search, which answers "which Pals" and cannot answer "does
        # this one". Routed here rather than given its own class because the info card
        # already holds the fact; it just has to lead with it.
        if not (_INFO_CUES.search(utterance) or _RIDE_ONE_CUE.search(utterance)):
            return None
        return ToolCall(name="get_pal_info", args={"pal": top.canonical},
                        rationale=f"info cue + pal candidate {top}")

    def _attribute_call(self, utterance: str,
                        candidates: list[Candidate]) -> "ToolCall | None":
        """`find_pals_by_attribute` when the utterance describes a Pal instead of naming one.

        **The guard is the absence of an entity, and it is the whole safety argument.**
        Every other class here takes a Pal or a resource the player said; this one takes
        a description. So if anything in the utterance clears its own floor as a named
        entity, this is not that question and the branch abstains - which also means it
        can never steal from Q1 or Q2, because those are exactly the queries that name
        something.

        That guard is not theoretical. STATUS records these four questions being asked
        and declined on 2026-08-11, and the near-misses that made them dangerous: on
        *"I need a new mining pal"* Anubis ranked **0.77** - the game's best mining Pal,
        so a location card for it would have read as very nearly a correct answer for
        entirely the wrong reason. Only the 0.85 floor stopped it. Below that floor
        `_subject` returns None and this branch answers the question actually asked.

        The word "pal" is required and the wake word is stripped first, because "hey pal,
        where can I go mining" is a location question and the address must not supply
        the noun that makes it look like a roster query.
        """
        if not self._attributes:
            return None
        body = _ADDRESS.sub("", utterance)
        # "mount" counts as the subject noun in its own right. "What's the fastest mount
        # I can get at 60" never says "pal" and is unambiguously this class; requiring
        # the word declined it. The wake word is still stripped first, because "hey pal"
        # must not be what supplies the noun.
        if not (_PAL_NOUN.search(body) or _MOUNT_CUE.search(body)):
            return None

        element = work = None
        if (m := _ELEMENT_CUE.search(body)):
            element = _ELEMENT_WORDS[(m.group(1) or m.group(2)).lower()]
        if (m := _WORK_CUE.search(body)):
            work = _WORK_WORDS[next(g for g in m.groups() if g).lower()]
        elif _POWER_CUE.search(body):
            work = "GenerateElectricity"
        elif _OIL_CUE.search(body):
            work = "OilExtraction"

        mount = bool(_MOUNT_CUE.search(body))
        medium = (next((name for name, cue in _MEDIUM_CUES if cue.search(body)), None)
                  if mount else None)

        # **An element word we could not attach means defer, not answer without it.**
        # "Which dragons can I ride at level 60" says `dragons`, which `_ELEMENT_CUE`
        # cannot claim because the pattern wants "dragon pal" or "dragon type". Answering
        # anyway returned every mount at level 60 under a card titled "Mounts" - a filter
        # the player stated, silently dropped, on the fast path. That is the same failure
        # as the drop branch's second-entity guard: an unresolved signal is a reason to
        # hand the sentence to something that can read it.
        #
        # `ground` and `water` name an element AND a medium, so a word the medium cue
        # already consumed does not count as unattached - otherwise "the fastest ground
        # mount" would defer for saying "ground".
        consumed = _MEDIUM_CUES_BY_NAME[medium] if medium else None
        loose = {w for w in _ELEMENT_WORDS
                 if re.search(rf"\b{w}s?\b", body, re.I)
                 and not (consumed and consumed.search(w))}
        if loose and element is None:
            return None

        level = _spoken_level(body)
        if element is None and work is None and not mount:
            # A level alone is not this class. "what level should Shroomer be" names a
            # Pal and asks something else entirely, and "any pals at level 60" is a list
            # of ninety, which is an index rather than an answer.
            return None
        if self._subject(candidates) is not None:
            return None

        args: dict[str, object] = {}
        if element:
            args["element"] = element
        if work:
            args["work"] = work
        if mount:
            args["mount"] = True
            if medium:
                args["medium"] = medium
            if _UNOWNED_CUE.search(body):
                args["unowned"] = True
            # **The level means the PLAYER's here, and only here.** A saddle is a
            # technology gated on player level, which is why STATUS's 2026-08-11 decision
            # is amended rather than kept whole: that decision rejected player level
            # because filtering by it needed an uncalibrated headroom constant, and a
            # saddle gate needs none - the game states the number. Two argument names so
            # the dispatcher cannot confuse them.
            if level is not None:
                args["player_level"] = level
        elif level is not None:
            args["level"] = level
        return ToolCall(name="find_pals_by_attribute", args=args,
                        rationale=f"attribute cue, no named entity: {args}")

    def _base_call(self, utterance: str,
                   candidates: list[Candidate]) -> "ToolCall | None":
        """`suggest_base_sites` when the utterance asks where to PUT a base.

        The only branch here that both names entities and preempts the location gate, so
        it is the only one whose safety rests entirely on its cue rather than on a
        structural guard. `_BASE_CUE` requires a placement verb for exactly that reason -
        see the comment on it.

        Several resources are allowed and that is the point of the class: "a base for ore
        and coal" is a question about one circle reaching two things, which no other tool
        here can express. Every named resource must clear the resource floor; one that
        does not means the sentence named something this router could not place, and
        answering about the rest would silently drop a filter the player stated - the
        failure the mount work found and the drop branch's second-entity guard already
        treats.
        """
        if not (self._base_sites and candidates):
            return None
        if not _BASE_CUE.search(utterance):
            return None
        # **`_locatable`, not `_resources`.** The two differ by exactly the case this
        # guard is for: `_resources` is everything the player can NAME and includes crude
        # oil, which has no placed nodes at all. Checking the wrong one let "a base for
        # crude oil" through with a resource the siting maths has nothing to measure.
        locatable = set(self._locatable)
        wanted, weak = [], False
        for c in candidates:
            if c.kind != "resource" or c.score < self._floor:
                continue
            if c.canonical in locatable:
                if c.canonical not in wanted:
                    wanted.append(c.canonical)
            else:
                # Named, recognised, and not something with map nodes. The siting question
                # cannot include it, and quietly answering about the others would drop a
                # filter the player stated.
                weak = True
        if not wanted or weak or len(wanted) > MAX_BASE_RESOURCES:
            return None
        return ToolCall(name="suggest_base_sites", args={"resources": wanted},
                        rationale=f"base placement cue + {len(wanted)} resource(s): "
                                  f"{', '.join(wanted)}")

    def _rate_call(self, utterance: str,
                   candidates: list[Candidate]) -> "ToolCall | None":
        """`rate_base_site` when the utterance asks how good a place is.

        Checked BEFORE `_base_call`, because "how good is this base spot" carries a
        `spot` and could look like siting, while nothing in the siting vocabulary asks
        how good anything is. The two are told apart by what they want, not by a shared
        word: this one names no resource and asks for a judgement.

        Abstains on a named entity like every other no-entity branch, which is what keeps
        "how good is Anubis" out of it - that is an info question about a Pal.
        """
        if not self._base_sites:
            return None
        body = _ADDRESS.sub("", utterance)
        if not (_RATE_CUE.search(body) and _RATE_SUBJECT.search(body)):
            return None
        if self._subject(candidates) is not None:
            return None
        args = {"own_base": True} if _OWN_BASE.search(body) else {}
        return ToolCall(name="rate_base_site", args=args,
                        rationale="base rating cue, no named entity"
                                  + (" (their own base)" if args else " (where they are)"))

    def _tech_call(self, utterance: str,
                   candidates: list[Candidate]) -> "ToolCall | None":
        """`suggest_next_unlock` when the utterance asks what to research.

        **The guard is the absence of a named entity**, the same structural argument
        attribute search makes: every other class here takes something the player said
        out loud, so an utterance that names one is not this question. That is what
        keeps "how do I unlock Anubis" and "where do I research coal" out of this branch
        without the pattern having to know anything about what follows the verb.

        A goal is optional and a level is optional, so unlike every other branch here
        this one can legitimately return a call with **no arguments at all** - "what
        should I research next" is a complete question. That is why the cue has to carry
        the whole decision, and why the vocabulary is narrow enough that it does.

        The level means the PLAYER's, which needs no amendment to STATUS's 2026-08-11
        decision beyond the one the mount work already made: a `LevelCap` is a gate the
        game states, so reading it is not the uncalibrated judgement that decision
        refused.
        """
        if not self._progression:
            return None
        body = _ADDRESS.sub("", utterance)
        # BOTH halves. The topic on its own claimed "can you explain technology points",
        # which this class cannot answer and which reads as an answer anyway.
        if not (_TECH_CUES.search(body) and _TECH_ASK.search(body)):
            return None
        if self._subject(candidates) is not None:
            return None

        args: dict[str, object] = {}
        if (m := _TECH_GOAL_CUE.search(body)):
            args["goal"] = _TECH_GOAL_WORDS[m.group(1).lower()]
        if _ANCIENT_CUE.search(body):
            args["currency"] = "ancient"
        if (level := _spoken_level(body)) is not None:
            args["player_level"] = level
        return ToolCall(name="suggest_next_unlock", args=args,
                        rationale=f"tech cue, no named entity: {args or 'no filters'}")

    def _corpus_call(self, utterance: str,
                     candidates: list[Candidate]) -> "ToolCall | None":
        """`lookup_corpus` when the utterance asks how something WORKS and we can say.

        See the comment on `_EXPLAIN_CUES` for both guards. The important one is the
        second: this branch consults the corpus before claiming, so a question it cannot
        ground still reaches the model.
        """
        if self._corpus is None:
            return None
        body = _ADDRESS.sub("", utterance)
        if not _EXPLAIN_CUES.search(body):
            return None
        if self._subject(candidates) is not None:
            return None
        if not self._corpus(body):
            return None
        return ToolCall(name="lookup_corpus", args={"query": body},
                        rationale="explanatory cue, no named entity, grounded in the "
                                  "game's own text")

    def _names_an_entity(self, candidates: list[Candidate]) -> bool:
        return self._subject(candidates) is not None

    def _inherit(self, utterance: str, candidates: list[Candidate],
                 context: list) -> ToolCall | None:
        """Reuse the last turn's tool, and its entity when this utterance names none.

        Only turns that produced a TOOL CALL are usable. A previous decline resolves
        nothing - "what about the alpha" after a decline has no referent, and inheriting
        the decline's best-guess candidate would manufacture one.
        """
        prior = next((t for t in reversed(context) if t.tool and t.entities), None)
        if prior is None:
            return None

        # A follow-up that names its own subject keeps only the VERB from the previous
        # turn, never the entity - and the subject decides the tool, not memory. "and
        # coal?" after a Pal query is a resource question; matching the remembered tool
        # instead answered it with the Pal, which is the confidently-wrong card this
        # whole feature is supposed to avoid.
        named = self._subject(candidates)
        if named is not None:
            tool, slot, canonical = named
            matched = next((c.matched_text for c in candidates
                            if c.canonical == canonical), "")
            if _residue(utterance, matched):
                # It names an entity AND carries its own content words, so it is a new
                # question with its own verb - "how about breeding Anubis" - and the verb
                # is not one this router knows. Inheriting "where is" from the last turn
                # would answer a breeding question with a map location. Fall through to
                # the ordinary cue gate, which will decline it.
                return None
            return ToolCall(name=tool, args={slot: canonical},
                            rationale=f"follow-up naming {canonical}; verb from: {prior}")

        if _residue(utterance):
            # It names something this router could not place - a mangled Pal name, most
            # often. Inheriting the previous turn's entity would quietly answer about
            # coal a question that was asked about a Pal, which is worse than deferring
            # to a model that can read the sentence.
            return None

        slot = "resource" if prior.tool == "find_resource_nodes" else "pal"
        if slot not in prior.entities:
            # The remembered turn used the other tool's slot, so there is nothing to
            # carry. Better to defer than to invent a subject.
            return None
        return ToolCall(name=prior.tool, args=dict(prior.entities),
                        rationale=f"follow-up, inherited from: {prior}")

    def route(self, utterance: str, candidates: list[Candidate],
              context: list | None = None) -> ToolCall | Decline:
        # A follow-up is handled before the cue gate, because most of them have no cue:
        # "what about the alpha?" and "and coal?" are location questions only by
        # inheritance from the previous turn, which is precisely what storing the tool is
        # for. Resolving here is not guessing at intent - it is reading the intent the
        # last turn already established.
        if _FOLLOWUP.search(utterance):
            if context:
                inherited = self._inherit(utterance, candidates, context)
                if inherited is not None:
                    return inherited
            # Nothing to inherit. If the utterance names no subject of its own, it is
            # referring to something that is gone, and ADR-0013 requires saying so rather
            # than silently ignoring it - answering "what about the alpha" against no
            # referent is how a confident card about the wrong Pal gets made.
            if not self._names_an_entity(candidates):
                return Decline(
                    reason="I've lost track of what that refers to",
                    needs_restatement=True)
            # It names something but there is no verb and no history - "and coal?" out of
            # nowhere. Not a restatement problem; fall through to the ordinary path.

        # Drops are checked BEFORE the location gate below, because a drop question has
        # no location cue by construction - "what does Vanwyrm drop" contains none of
        # `where|nearest|find|...`, so the gate declined it before this branch could see
        # it. Measured: the branch claimed exactly nothing until it moved up here.
        # Counters go above the location gate for the same reason drops do - "how do I
        # beat Anubis" carries no `where|nearest|find` - and above drops because the two
        # cue sets do not overlap, so the order between them is arbitrary and this one
        # is the more selective.
        counters = self._counter_call(utterance, candidates)
        if counters is not None:
            return counters

        drops = self._drops_call(utterance, candidates)
        if drops is not None:
            return drops

        # Attribute search goes above the location gate for the third time in a row and
        # for a new reason: "give me an electric pal that is level 60" DOES carry a wide
        # cue ("give me"), so the gate lets it through and then the branches below find
        # no entity to answer about and decline. It goes last among the three because its
        # guard is the strictest - it requires every other branch to have found nothing
        # nameable - so nothing it claims was ever available to them.
        # Behind counters and drops, ahead of the location gate. "What's Victor's
        # weakness" and "what do I get from X" are more specific readings of the same
        # polite openers, and this branch must not claim them; "tell me about Shroomer"
        # carries no location cue, so the gate below would decline it.
        info = self._info_call(utterance, candidates)
        if info is not None:
            return info

        # Above attribute search because its cue vocabulary is disjoint from every other
        # family here and strictly narrower than attribute search's - "what tech should I
        # research for my mining pals" carries a job word AND the word "pal", so the
        # attribute branch would claim it and answer a technology question with a roster.
        # Nothing goes the other way: no attribute cue mentions research or unlocking.
        tech = self._tech_call(utterance, candidates)
        if tech is not None:
            return tech

        # Above the location gate because "where should I build my base for coal" carries
        # `where` and a confident `coal`, so the gate would pass it straight to the
        # resource branch and answer a siting question with a list of coal spots. The one
        # branch here whose safety is the cue alone - see `_base_call`.
        # Ahead of siting: "how good is this base spot" carries a word siting looks for,
        # and nothing in siting's vocabulary asks how good anything is.
        rating = self._rate_call(utterance, candidates)
        if rating is not None:
            return rating

        bases = self._base_call(utterance, candidates)
        if bases is not None:
            return bases

        attribute = self._attribute_call(utterance, candidates)
        if attribute is not None:
            return attribute

        # Last of the pre-gate branches, and deliberately: it is the broadest, its subject
        # is a mechanic rather than an entity, and every branch above it answers a
        # narrower reading of the same explanatory phrasings.
        grounded = self._corpus_call(utterance, candidates)
        if grounded is not None:
            return grounded

        if not self._cues.search(utterance):
            # Name what we *can* answer here too. The other two branches always did, and
            # the difference only became visible once this decline could reach a player
            # via the transport fallback rather than only a test.
            return Decline(
                reason="no location intent recognised",
                unrecognized=None,
                known_options=list(self._locatable))

        # The corrector deliberately ranks without a threshold, leaving the confidence
        # judgement to the router (ADR-0016). That assumes a router that can reason.
        # This one cannot, so it applies its own bar - without it, "where can I find
        # Suzaku" answered with a coal location, because *some* resource always appears
        # somewhere in a top-10 candidate list.
        # MIN_CONFIDENT here, never self._floor: the guard must not loosen when the
        # backstop does. A permissive backstop should answer weaker RESOURCE matches, not
        # become quicker to call something a Pal question and give up.
        top = candidates[0] if candidates else None

        if top is not None and top.kind == "pal" and top.score >= MIN_CONFIDENT:
            # A confidently-matched Pal means the query is about a Pal. Through Phase 1
            # that was the end of it - no Pal tool existed, so the honest move was to say
            # so rather than reach for a weak resource. `find_pal_spawns` changes what
            # follows the same judgement, not the judgement itself.
            #
            # The bar is MIN_CONFIDENT, not self._floor. A permissive backstop should
            # answer weaker RESOURCE matches; it must not become quicker to decide an
            # utterance names a Pal, because that decision also steals the query from the
            # resource branch below.
            if (self._pal_spawns and top.score >= self._pal_floor
                    and self._pal_cues.search(utterance)):
                return ToolCall(
                    name="find_pal_spawns",
                    args={"pal": top.canonical},
                    rationale=f"location cue + pal candidate {top}",
                )
            if self._pal_spawns:
                # Confident enough that this is a Pal question, not confident enough
                # about WHICH Pal. Defer: the model reads sentence context and recovers
                # mangled names the ranker cannot (ADR-0016), and a spawn card naming
                # the wrong species is the failure ADR-0007 refuses to ship.
                return Decline(
                    reason=f"a Pal question, but {top.canonical} is only a "
                           f"{top.score:.2f} match")
            return Decline(
                reason=f"that looks like a question about {top.canonical}, "
                       f"and I can only find resources so far",
                known_options=list(self._locatable))

        resource = next((c for c in candidates
                         if c.kind == "resource" and c.canonical in self._resources
                         and c.score >= self._floor),
                        None)
        if resource is None:
            # Deliberately NOT reporting the top candidate's matched text as the
            # "unrecognized" token - that is whatever scored highest, which for an
            # unknown word is usually an unrelated part of the sentence. Naming what we
            # can answer is both honest and actionable.
            return Decline(reason="no resource identified",
                           known_options=list(self._locatable))

        args: dict[str, object] = {"resource": resource.canonical}
        # Here the number means the PLAYER's level - see `_spoken_level`.
        if (level := _spoken_level(utterance)) is not None:
            args["max_player_level"] = level

        return ToolCall(
            name="find_resource_nodes",
            args=args,
            rationale=f"location cue + resource candidate {resource}",
        )
