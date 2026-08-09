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

        # Only resource candidates are meaningful for the one registered tool.
        resource = next((c for c in candidates
                         if c.kind == "resource" and c.canonical in self._resources),
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
