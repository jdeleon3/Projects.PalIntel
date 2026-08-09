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

import re
from typing import Protocol

from .knowledge import Candidate, Lexicon
from .tools import Decline, ToolCall

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


# --------------------------------------------------------------------------- stub

# Phrasings that clearly ask for a location. Deliberately narrow: the stub exists to
# unblock downstream work and to serve as the fast path later (Phase 5), NOT to be a
# substitute for the model. Anything outside these patterns declines rather than
# guessing, which keeps the stub honest about its own coverage.
_LOCATION_CUES = re.compile(
    r"\b(where|nearest|closest|find|locate|show me|spot|deposit|node)\b", re.I)
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

    Used to build and test the pipeline before a model is chosen, and to give the
    Phase 5 fast path a starting point.
    """

    name = "stub"

    def __init__(self, lexicon: Lexicon, locatable: set[str] | None = None):
        # Recognised and locatable are different sets. Crude oil is recognised - the
        # player can name it and deserves a real answer - but has no map locations, so
        # offering it as something we can "find" would be misleading.
        self._resources = set(lexicon.resources())
        self._locatable = locatable if locatable is not None else self._resources

    def route(self, utterance: str, candidates: list[Candidate]) -> ToolCall | Decline:
        if not _LOCATION_CUES.search(utterance):
            return Decline(
                reason="no location intent recognised",
                unrecognized=None)

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
