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


class RouterBackend(Protocol):
    """Anything that can turn an utterance into a tool call or an honest decline."""

    name: str

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
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

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        call = self.fast.route(utterance, candidates)
        if isinstance(call, ToolCall):
            log.info("fast path: %s(%s) - no model call", call.name, call.args)
            return call
        return self.full.route(utterance, candidates)


class FallbackRouter:
    """A router with a deterministic backstop for when the hosted one does not answer.

    Only `transient` declines fall through - a timeout, a rate limit, a 5xx. A considered
    decline is the model's answer and is passed on untouched, because the stub has strictly
    less information than the model did and re-deciding on less is how a "no" becomes a
    confidently wrong "yes".

    The point is not to salvage latency. A request that timed out has already blown the
    2.5s budget several times over, and nothing recovers that. It is that the player gets
    a card either way: on Q1 the stub answers a clear resource query outright, and where
    it cannot, it names what it can answer instead of leaving the bot looking hung.
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

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        call = self.primary.route(utterance, candidates)
        if isinstance(call, Decline) and call.transient:
            log.warning("%s did not answer (%s) - falling back to %s",
                        self.primary.name, call.reason, self.backstop.name)
            fallback = self.backstop.route(utterance, candidates)
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
# Nothing was stolen at any width and no resource was ever wrong. Treat that with care:
# 15 prompts is a thin basis, and the zero outside Q1 is the number most likely to move
# in Phase 2, when `find_pal_spawns` finally gives a wider list something to steal.
# `wide` guesses at intent ("i need", "any") where the others only name places, so it is
# the one to revisit first. See router.cues in config.
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


class StubRouter:
    """Deterministic keyword router. No model, no network.

    Used to build and test the pipeline before a model is chosen, as the transport
    backstop, and as the Q1 fast path.
    """

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None,
                 cues: str = DEFAULT_CUES):
        # Recognised and locatable are different sets. Crude oil is recognised - the
        # player can name it and deserves a real answer - but has no map locations, so
        # offering it as something we can "find" would be misleading.
        self._resources = set(lexicon.resources())
        self._locatable = locatable if locatable is not None else self._resources
        if cues not in _CUE_SETS:
            raise ValueError(f"unknown cue set {cues!r}, expected one of "
                             f"{sorted(_CUE_SETS)}")
        self._cues = re.compile(rf"\b({_CUE_SETS[cues]})\b", re.I)
        # The width is in the name so it reaches `/palintel status` and every routing
        # log line. A fast path that quietly widened would be indistinguishable from the
        # model getting worse.
        self.name = f"stub:{cues}"

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        if not self._cues.search(utterance):
            # Name what we *can* answer here too. The other two branches always did, and
            # the difference only became visible once this decline could reach a player
            # via the transport fallback rather than only a test.
            return Decline(
                reason="no location intent recognised",
                unrecognized=None,
                known_options=sorted(self._locatable))

        # The corrector deliberately ranks without a threshold, leaving the confidence
        # judgement to the router (ADR-0016). That assumes a router that can reason.
        # This one cannot, so it applies its own bar - without it, "where can I find
        # Suzaku" answered with a coal location, because *some* resource always appears
        # somewhere in a top-10 candidate list.
        top = candidates[0] if candidates else None
        if top is not None and top.kind == "pal" and top.score >= MIN_CONFIDENT:
            # A confidently-matched Pal means the query is about a Pal, and no Pal tool
            # is registered yet. Say that, rather than reaching for a weak resource.
            return Decline(
                reason=f"that looks like a question about {top.canonical}, "
                       f"and I can only find resources so far",
                known_options=sorted(self._locatable))

        resource = next((c for c in candidates
                         if c.kind == "resource" and c.canonical in self._resources
                         and c.score >= MIN_CONFIDENT),
                        None)
        if resource is None:
            # Deliberately NOT reporting the top candidate's matched text as the
            # "unrecognized" token - that is whatever scored highest, which for an
            # unknown word is usually an unrelated part of the sentence. Naming what we
            # can answer is both honest and actionable.
            return Decline(reason="no resource identified",
                           known_options=sorted(self._locatable))

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
