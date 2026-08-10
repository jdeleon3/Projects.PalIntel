"""The pipeline — one place where understanding meets answering.

Shared by every input channel. Text and voice converge here; voice simply arrives with
wake-word detection and transcription already done (Docs/adr/0012-dual-input-channels.md).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import cards, execution
from .cards import Card
from .knowledge import Candidate, KnowledgeBase
from .memory import Memory, Turn
from .routing import RouterBackend
from .tools import Decline, ToolCall

if TYPE_CHECKING:  # config imports nothing from here; the runtime import is inside
    from .artwork import Artwork
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


# Encounter-kind and time-of-day modifiers, read off the utterance rather than asked of
# the model. See routing_anthropic.pal_spawn_schema for why they are not tool parameters:
# strict tool use has no clean nullable-enum form, and deriving them here means the fast
# path and the model path agree by construction instead of by luck.
#
# "boss" is deliberately absent. Players call tower bosses, raid bosses and field alphas
# all "boss", and only the last is in this dataset - so matching it would answer "where's
# the Zoe boss" with a field location for a tower fight. "alpha" and "lord" are
# unambiguous; "boss" is not, and the model still sees the word and can route on it.
_ALPHA = re.compile(r"\b(alpha|lord)\b", re.I)
_PREDATOR = re.compile(r"\bpredator\b", re.I)
_NIGHT = re.compile(r"\b(at night|night-?time|nocturnal|after dark)\b", re.I)


def spawn_kind(utterance: str) -> str | None:
    """Which encounter the phrasing asks for, or None to fall through the kinds."""
    if _ALPHA.search(utterance):
        return "alpha"
    if _PREDATOR.search(utterance):
        return "predator"
    return None


@dataclass
class Outcome:
    """Everything about one query, for the cards and for diagnosis."""
    cards: list[Card]
    call: ToolCall | Decline
    candidates: list[Candidate]
    # Deferred artwork. The cards are complete without it; calling this fills in their
    # `image` and `thumbnail`. Deliberately NOT done during `handle`: a map crop is 8-45
    # ms and the widest one measured 472 ms before the zoom levels were added, and all of
    # that would have sat in front of the text card the player is actually waiting for.
    # The caller posts the answer, then draws.
    illustrate: "Callable[[], None] | None" = None

    @property
    def card(self) -> Card:
        """The first card. Convenience for callers that only ever show one."""
        return self.cards[0]

    def draw(self) -> None:
        """Attach artwork, if any was planned. Safe to call always, and to call once."""
        if self.illustrate is not None:
            self.illustrate()
            self.illustrate = None


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

    # Ordered by how much of the map backs each one, because this list is what a decline
    # card offers the player as "what I can find". Alphabetical put Ancient Bark, Ancient
    # Bone and Ancient Lava first - seven clusters each, on Feybreak, nobody's question -
    # and buried stone, wood and ore below them.
    by_count: dict[str, int] = {}
    for n in kb.nodes:
        by_count[n.resource] = by_count.get(n.resource, 0) + 1
    locatable = sorted(by_count, key=lambda r: (-by_count[r], r))

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
    # Both now register `find_pal_spawns`, gated at PAL_CONFIDENT and on the narrower cue
    # set. Measured over the A5 transcripts, adding the Pal class to either stub claimed
    # nothing outside the two query classes and produced no wrong card - which is the
    # result that had to be established rather than assumed, since Phase 1's "claimed
    # nothing outside Q1" was scored when there was no other tool to claim for.
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
    def __init__(self, kb: KnowledgeBase, router: RouterBackend,
                 memory: "Memory | None" = None, artwork: "Artwork | None" = None):
        self.kb = kb
        self.router = router
        # Illustration is a decoration applied after the cards are built, never a step
        # they depend on. None means text-only, which is what every existing test and
        # the CLI harness get.
        self.artwork = artwork
        # ADR-0013. Constructed by default so every entry point gets follow-ups without
        # having to opt in - the CLI harness included, since that is where they get
        # debugged.
        self.memory = memory if memory is not None else Memory()

    def handle(self, utterance: str, state: PlayerState | None = None,
               who: str = "local") -> Outcome:
        state = state or PlayerState()

        # 1. Rank entities. Ranking only - the decline decision belongs to the router,
        #    which has sentence context (ADR-0016).
        candidates = self.kb.lexicon.rank(utterance)

        # 2. Route, with whatever this speaker said recently. Per user, so two people
        #    asking at once cannot resolve each other's pronouns.
        context = self.memory.recent(who)
        call = self.router.route(utterance, candidates, context)
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
            self._remember(who, call, {"resource": result.resource},
                           f"{len(result.nodes)} of {result.total_available} clusters")
            card = cards.resource_card(result)
            draw = (self.artwork.illustrate_resource(card, result)
                    if self.artwork is not None else None)
            return Outcome([card], call, candidates, illustrate=draw)

        if call.name == "find_pal_spawns":
            pal = call.args.get("pal")
            if not pal:
                # Same failure the resource tool sees: a model can name a registered tool
                # and omit its only required argument. An honest decline, not a TypeError.
                log.warning("router called %s with no pal: %s", call.name, call.args)
                return self._decline(
                    Decline(reason="no Pal identified"), candidates)

            kind = spawn_kind(utterance)
            night = True if _NIGHT.search(utterance) else None
            near = state.player_coords

            # One per card actually built. A family that renders two cards has two
            # pictures to draw, and a clarifying question has none - it never calls
            # `render`, so nothing is planned for a card that shows no answer.
            planned: list[Callable[[], None]] = []

            def render(name: str) -> Card:
                result = execution.find_pal_spawns(
                    self.kb, name, kind=kind, near=near, night=night)
                log.info("find_pal_spawns(%s, kind=%s, night=%s) -> %d/%d",
                         name, kind, night, len(result.areas), result.total_available)
                card = cards.spawn_card(result)
                if self.artwork is not None:
                    planned.append(self.artwork.illustrate_spawn(card, result))
                return card

            # A Paldeck slot holding a base Pal and its element variant has two correct
            # answers, and they spawn in different places - Menasting in the desert,
            # Menasting Terra in the dunes. Answering only the base would be wrong half
            # the time, so both render.
            #
            # Only the Pal the router actually named is remembered, not the family. The
            # variants were shown because we could not narrow the question; treating that
            # as though the speaker had named both would let "what about the alpha" pick
            # the wrong one of the two.
            self._remember(who, call, {"pal": pal},
                           f"{kind or 'normal'} spawns")
            built = self._cards_for(self.kb.lexicon.family(pal), render)

            def draw_all() -> None:
                for one in planned:
                    one()

            return Outcome(built, call, candidates,
                           illustrate=draw_all if planned else None)

        if call.name == "find_pal_drops":
            pal = call.args.get("pal")
            if not pal:
                # Same shape as the other two: a model can name a registered tool and
                # omit its only required argument. An honest decline, not a TypeError.
                log.warning("router called %s with no pal: %s", call.name, call.args)
                return self._decline(Decline(reason="no Pal identified"), candidates)

            def render_drops(name: str) -> Card:
                result = execution.find_pal_drops(self.kb, name)
                log.info("find_pal_drops(%s) -> %d items", name, result.total)
                card = cards.drops_card(result)
                if self.artwork is not None and self.artwork.icons:
                    # The icon only. There is nothing to map - a drop table is not a
                    # place - so this card asks for no crop.
                    card.thumbnail = self.artwork.assets.icon(name)
                return card

            self._remember(who, call, {"pal": pal}, "drops")
            return Outcome(self._cards_for(self.kb.lexicon.family(pal), render_drops),
                           call, candidates)

        # A tool the router knows about but the dispatcher does not is a wiring bug.
        # Fail loudly here rather than rendering something plausible.
        raise RuntimeError(f"router produced unregistered tool: {call.name!r}")

    def _remember(self, who: str, call: ToolCall, entities: dict[str, str],
                  summary: str) -> None:
        """Record an ANSWERED turn. Declines are deliberately not stored.

        A decline resolved nothing, so it has no referent to offer a follow-up - and
        storing its best-guess candidate would manufacture one, which is precisely the
        failure ADR-0013 warns about. After a decline, "what about the alpha" correctly
        reaches back past it to the last real answer, or asks for restatement.
        """
        self.memory.remember(Turn(who=who, tool=call.name, entities=entities,
                                  summary=summary))

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
