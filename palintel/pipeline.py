"""The pipeline — one place where understanding meets answering.

Shared by every input channel. Text and voice converge here; voice simply arrives with
wake-word detection and transcription already done (Docs/adr/0012-dual-input-channels.md).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import cards, execution
from .cards import Card
from .knowledge import Candidate, KnowledgeBase
from .routing import RouterBackend
from .tools import Decline, ToolCall

if TYPE_CHECKING:  # config imports nothing from here; the runtime import is inside
    from .config import RouterConfig

log = logging.getLogger("palintel.pipeline")


@dataclass(frozen=True)
class PlayerState:
    """Live game state, read from the save. Every field is optional and absent is normal.

    `player_coords` is the position at the last autosave, in map units - what "nearest"
    resolves against. It lags the player by up to one autosave interval, which is
    inherent to reading a save rather than the game's memory and fine for a question
    answered against a region.

    `player_level` is still always None: it lives in a `Level.sav` blob whose decoder is
    stale for 1.0.2 (see saves.py), so level gating is not yet on.
    """
    player_level: int | None = None
    player_coords: tuple[float, float] | None = None


# One answer may be several cards. A Paldeck slot holds a base Pal and its element
# variant - Menasting and Menasting Terra are DarkScorpion and DarkScorpion_Ground - and
# they have different elements, spawns and combos, so a query that cannot be narrowed to
# one of them has two correct answers rather than one ambiguous one. On the output
# surface (Discord, second screen) that renders as two titled cards the reader picks
# between; it was only unworkable when the target was a cramped in-game overlay, which
# A1's retirement removed.
#
# Beyond this many, the answer stops being a set of options and becomes a wall, so the
# pipeline asks a clarifying question instead. Variant families are always exactly 2, so
# the cap only binds on multi-entity queries where more than one slot is ambiguous.
MAX_CARDS = 2


@dataclass
class Outcome:
    """Everything about one query, for the cards and for diagnosis."""
    cards: list[Card]
    call: ToolCall | Decline
    candidates: list[Candidate]

    @property
    def card(self) -> Card:
        """The first card. Convenience for callers that only ever show one."""
        return self.cards[0]


def build_router(kb: KnowledgeBase, prefer: str = "auto",
                 router_config: "RouterConfig | None" = None) -> RouterBackend:
    """Select a router backend.

    `auto` tries Gemini, then Claude, then the stub, so the pipeline stays runnable with
    no credential at all. Gemini leads because it was measured to: on 232 utterances it
    scored 88.8% exact against Haiku 4.5's 72.9%, winning 35 paired comparisons to 3
    (McNemar p = 6.7e-08), with a lower wrong-entity rate and half the latency. See the
    A5 tables in Docs/04-roadmap.md. Opus 5 is not in the chain - it was dropped on cost
    after Gemini beat it 35-0 on the same test.

    Whichever is chosen gets two deterministic wrappers, both configurable off:
    a fast path in front of it (`router.fast_path`) and a transport backstop behind it.

    Every fallback is logged rather than silent. A router quietly downgrading to keyword
    matching would look like a capability regression with no visible cause.
    """
    from .config import RouterConfig
    from .routing import (BACKSTOP_CONFIDENT, FallbackRouter, FastPathRouter,
                          StubRouter)

    cfg = router_config or RouterConfig()
    locatable = {n.resource for n in kb.nodes}

    # Two stubs, and the asymmetry is the whole point.
    #
    # The fast path preempts a working model, so it must be strict: everything it claims
    # is a query the model never sees. The backstop runs only when the model did not
    # answer at all, so its alternative is not a better answer but nothing, and it can
    # afford a lower resource floor.
    #
    # They were the same instance until a session showed the backstop could not rescue a
    # single query: the fast path had already asked that exact stub the same question, so
    # the fallthrough was guaranteed to re-decline. Sharing looked like the careful choice
    # and was actually what made the safety net decorative.
    fast = StubRouter(kb.lexicon, locatable, cues=cfg.cues)
    backstop = StubRouter(kb.lexicon, locatable, cues="wide",
                          resource_floor=BACKSTOP_CONFIDENT)

    def wrapped(primary):
        """Wrap a hosted router with the backstop, and the fast path if enabled.

        Only the hosted backends get this. The stub cannot back itself up, and the local
        backend already fails against a server on this machine rather than the network.
        """
        routed = FallbackRouter(primary, backstop)
        return FastPathRouter(fast, routed) if cfg.fast_path else routed

    if prefer in ("auto", "gemini"):
        try:
            from . import routing_gemini
            if routing_gemini.available():
                return wrapped(
                    routing_gemini.GeminiRouter(kb.lexicon, locatable))
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
                return wrapped(
                    routing_anthropic.ClaudeRouter(kb.lexicon, locatable))
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

    return fast


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
            return self._decline(call, candidates)

        # 3. Dispatch. Player state is injected here, never parsed from the utterance -
        #    "nearest" must resolve against where the player actually is.
        if call.name == "find_resource_nodes":
            args = dict(call.args)
            # The model can name a registered tool and still omit a required argument -
            # observed on Gemini, which answers "how do I breed Vanwyrm" with an empty
            # find_resource_nodes call rather than declining. That is the model failing
            # to decline, not a wiring fault, so it becomes an honest decline instead of
            # a TypeError out of the dispatcher.
            if not args.get("resource"):
                log.warning("router called %s with no resource: %s", call.name, call.args)
                return self._decline(
                    Decline(reason="no resource identified",
                            known_options=sorted({n.resource for n in self.kb.nodes})),
                    candidates)
            if state.player_coords is not None:
                args.setdefault("near", state.player_coords)
            if state.player_level is not None:
                args.setdefault("max_player_level", state.player_level)

            result = execution.find_resource_nodes(self.kb, **args)
            log.info("find_resource_nodes(%s) -> %d/%d",
                     args, len(result.nodes), result.total_available)
            return Outcome([cards.resource_card(result)], call, candidates)

        # A tool the router knows about but the dispatcher does not is a wiring bug.
        # Fail loudly here rather than rendering something plausible.
        raise RuntimeError(f"router produced unregistered tool: {call.name!r}")

    def _decline(self, call: Decline, candidates: list[Candidate]) -> Outcome:
        log.info("decline: %s", call.reason)
        return Outcome([cards.decline_card(call)], call, candidates)

    def _cards_for(self, entities: list[str], render) -> list[Card]:
        """One card per entity, or a clarifying question past the cap.

        Not reachable from Q1 - a resource query names one resource, and variant families
        are a Pal concept. It becomes live with `find_pal_spawns` in Phase 2; it lives
        here now because the shape of Outcome had to change either way, and changing it
        under the voice work would have been worse.
        """
        if len(entities) > MAX_CARDS:
            return [cards.clarify_card(entities)]
        return [render(e) for e in entities]
