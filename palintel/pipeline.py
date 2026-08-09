"""The pipeline — one place where understanding meets answering.

Shared by every input channel. Text and voice converge here; voice simply arrives with
wake-word detection and transcription already done (Docs/adr/0012-dual-input-channels.md).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from . import cards, execution
from .cards import Card
from .knowledge import Candidate, KnowledgeBase
from .routing import RouterBackend
from .tools import Decline, ToolCall

log = logging.getLogger("palintel.pipeline")


@dataclass(frozen=True)
class PlayerState:
    """Live game state. Populated by the save watcher; absent until then."""
    player_level: int | None = None
    base_coords: tuple[float, float] | None = None


@dataclass
class Outcome:
    """Everything about one query, for the card and for diagnosis."""
    card: Card
    call: ToolCall | Decline
    candidates: list[Candidate]


def build_router(kb: KnowledgeBase, prefer: str = "auto") -> RouterBackend:
    """Select a router backend.

    `auto` tries Gemini, then Claude, then the stub, so the pipeline stays runnable with
    no credential at all. Gemini leads because it was measured to: on 232 utterances it
    scored 88.8% exact against Haiku 4.5's 72.9%, winning 35 paired comparisons to 3
    (McNemar p = 6.7e-08), with a lower wrong-entity rate and half the latency. See the
    A5 tables in Docs/04-roadmap.md. Opus 5 is not in the chain - it was dropped on cost
    after Gemini beat it 35-0 on the same test.

    Every fallback is logged rather than silent. A router quietly downgrading to keyword
    matching would look like a capability regression with no visible cause.
    """
    from .routing import StubRouter

    locatable = {n.resource for n in kb.nodes}

    if prefer in ("auto", "gemini"):
        try:
            from . import routing_gemini
            if routing_gemini.available():
                return routing_gemini.GeminiRouter(kb.lexicon, locatable)
            if prefer == "gemini":
                raise RuntimeError(
                    "No Gemini credential found. Set GEMINI_API_KEY in .env.")
            log.info("no Gemini credential - trying Claude")
        except ImportError:
            if prefer == "gemini":
                raise
            log.info("could not load the Gemini backend - trying Claude")

    if prefer in ("auto", "claude"):
        try:
            from . import routing_anthropic
            if routing_anthropic.available():
                return routing_anthropic.ClaudeRouter(kb.lexicon, locatable)
            if prefer == "claude":
                raise RuntimeError(
                    "No Anthropic credential found. Set ANTHROPIC_API_KEY or run "
                    "`ant auth login`.")
            log.info("no Anthropic credential - falling back to the stub router")
        except ImportError:
            if prefer == "claude":
                raise
            log.info("anthropic SDK not installed - falling back to the stub router")

    if prefer == "local":
        from . import routing_local
        if not routing_local.available():
            raise RuntimeError("No local model server - start `ollama serve`.")
        return routing_local.LocalRouter(kb.lexicon, locatable)

    return StubRouter(kb.lexicon, locatable)


class Pipeline:
    def __init__(self, kb: KnowledgeBase, router: RouterBackend):
        self.kb = kb
        self.router = router

    def handle(self, utterance: str, state: PlayerState | None = None) -> Outcome:
        state = state or PlayerState()

        # 1. Rank entities. Ranking only - the decline decision belongs to the router,
        #    which has sentence context (ADR-0016).
        candidates = self.kb.lexicon.rank(utterance)

        # 2. Route.
        call = self.router.route(utterance, candidates)
        if isinstance(call, Decline):
            log.info("decline: %s", call.reason)
            return Outcome(cards.decline_card(call), call, candidates)

        # 3. Dispatch. Player state is injected here, never parsed from the utterance -
        #    "nearest" must resolve against where the player actually is.
        if call.name == "find_resource_nodes":
            args = dict(call.args)
            if state.base_coords is not None:
                args.setdefault("near", state.base_coords)
            if state.player_level is not None:
                args.setdefault("max_player_level", state.player_level)

            result = execution.find_resource_nodes(self.kb, **args)
            log.info("find_resource_nodes(%s) -> %d/%d",
                     args, len(result.nodes), result.total_available)
            return Outcome(cards.resource_card(result), call, candidates)

        # A tool the router knows about but the dispatcher does not is a wiring bug.
        # Fail loudly here rather than rendering something plausible.
        raise RuntimeError(f"router produced unregistered tool: {call.name!r}")
