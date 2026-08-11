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
from typing import Protocol

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
_COUNTER_CUES = re.compile(
    r"\b(counter|counters|beat|defeat|kill|fight|fighting|"
    r"weak(?:ness)? (?:to|against)|take on)\b", re.I)

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
                 counterable: set[str] | None = None):
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
        # Width, floor and registered classes are all in the name so they reach
        # `/palintel status` and every routing log line. A fast path that quietly widened,
        # or a backstop quietly answering on weaker matches, would be indistinguishable
        # from the model getting worse.
        self.name = (f"stub:{cues}"
                     + (f"@{resource_floor:g}" if resource_floor != MIN_CONFIDENT else "")
                     + (f"+pals@{pal_floor:g}" if pal_spawns else ""))

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
        """
        if not (self._counters and candidates):
            return None
        if not _COUNTER_CUES.search(utterance):
            return None
        top = candidates[0]
        if top.kind != "pal" or top.score < self._pal_floor:
            return None
        if top.canonical.lower() not in self._counterable:
            return None

        # `self._cues` is the *wide* set on purpose: the question is whether any hint of
        # a location question is present, not whether the narrow gate would have claimed
        # it. Chained only when the spawn tool is actually registered - otherwise this
        # would name a tool the dispatcher does not have.
        also_a_location = bool(self._cues.search(utterance)) and self._pal_spawns
        chained = (ToolCall(name="find_pal_spawns", args={"pal": top.canonical},
                            rationale="location cue alongside a counter cue")
                   if also_a_location else None)
        return ToolCall(name="plan_counters", args={"boss": top.canonical},
                        rationale=f"counter cue + boss-capable pal {top}",
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
        if (m := _LEVEL.search(utterance)):
            args["max_player_level"] = int(m.group(1))
        else:
            for word, value in _LEVEL_WORDS.items():
                if re.search(rf"\blevel\s+{word}\b", utterance, re.I):
                    args["max_player_level"] = value
                    break

        return ToolCall(
            name="find_resource_nodes",
            args=args,
            rationale=f"location cue + resource candidate {resource}",
        )
