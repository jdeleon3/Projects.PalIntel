"""The pipeline — one place where understanding meets answering.

Shared by every input channel. Text and voice converge here; voice simply arrives with
wake-word detection and transcription already done (Docs/adr/0012-dual-input-channels.md).
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import cards, execution
from .cards import Card
from .knowledge import REPO, Candidate, KnowledgeBase
from .memory import Memory, Turn
from .routing import RouterBackend, coordinates
from .tools import Decline, ToolCall

if TYPE_CHECKING:  # config imports nothing from here; the runtime import is inside
    from .artwork import Artwork
    from .config import RouterConfig
    from .progression import PlayerTech

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
    # Owned Pal species, lower-cased, from `saves.owned_species`. None means NOT READ,
    # which is different from "owns nothing" and must stay different all the way to the
    # card: telling a player nothing they own works, when we never looked, is exactly
    # the confidently-wrong answer this project refuses.
    #
    # Absent by default because reading it costs a full Level.sav parse - seconds, not
    # milliseconds - so it cannot happen per query. `SaveWatcher.poll_roster` fills it on
    # a slow timer, which is what the bot passes in.
    owned_species: frozenset[str] | None = None
    # Q6's half of the save: unlocked technologies, both point pools, and which towers
    # have been beaten. Its own object rather than four more fields here, because they
    # are read together, used together, and absent together - and because `player_level`
    # sitting next to a technology's required level in one flat namespace is exactly the
    # confusion the mount amendment had to spell its way out of.
    #
    # None means the save was never read. `progression.PlayerTech()` with everything
    # absent means it was read and holds nothing, and the two produce different cards.
    tech: "PlayerTech | None" = None
    # Where the player's base camps are, in map units, from the same Level.sav read the
    # roster comes from. None means not read; an empty list means read and they have
    # none, and "rate my base" has to tell those apart.
    base_camps: list[tuple[float, float]] | None = None


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


def _counterable(version: str) -> set[str]:
    """Display names that name a fight, lower-cased. Empty when bosses.json is missing.

    **The counter fast path was dark until 2026-08-11.** `StubRouter` grew the branch,
    `score_branches.py` measured it at 16/16 on the written prompts it can claim, and
    `build_router` never passed `counters=True` - so every counter question in play paid
    a model round trip for an answer the stub had. That was an omission rather than a
    decision: no commit or ADR argues for leaving it off, and the branch abstains
    wherever the tier is in doubt, which is what the measurement was for.
    """
    import json

    path = REPO / "data" / version / "bosses.json"
    if not path.exists():
        log.info("no bosses.json - the counter fast path stays off")
        return set()
    bosses = json.loads(path.read_text(encoding="utf-8"))
    names = {b["name"].lower() for b in bosses["entries"] if b.get("name")}
    # And the game's own name for each tower fight - "Axel & Orserk". It is a PAL_NAME_
    # row, so the lexicon ranks it as a Pal and the fast path checks it against this set;
    # without it the most explicit way there is to name a tower paid a model round trip,
    # which is what it did in play on 2026-08-11.
    return names | {l["display"].lower() for l in bosses.get("leaders", [])}


def _has_dataset(version: str, filename: str) -> bool:
    """Whether an optional dataset was built, for gating the branch that needs it.

    A branch naming a tool whose data is missing is worse than one that is off: the
    router claims the query, the dispatcher declines, and the model never sees a question
    it could have answered.
    """
    present = (REPO / "data" / version / filename).exists()
    if not present:
        log.info("no %s - the branch that needs it stays off", filename)
    return present


def _corpus_probe(version: str):
    """A `query -> bool` the fast path can ask before claiming a Q7 question, or None.

    Returning a callable rather than a flag is what lets the branch claim only what it
    can ground. The corpus is loaded once here and cached, so the probe is a scan over
    3,106 chunks and no I/O.
    """
    from . import corpus

    try:
        loaded = corpus.load(version)
    except corpus.CorpusError:
        log.info("no corpus.json - the Tier 3 lookup branch stays off")
        return None
    return lambda query: loaded.search(query, limit=1).grounded


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
    # Which Pals have a boss form, read from the dataset rather than re-derived here.
    # `BOSS_<name>` meaning "the alpha of" is the inference CLAUDE.md flags by name, and
    # bosses.json already made it and recorded that it did.
    #
    # Empty when the dataset is absent, which turns the counter branch off rather than
    # failing: every other class still answers, and `plan_counters` is the only one that
    # needs it.
    counterable = _counterable(kb.game_version)
    # Same gate, same reason, and the same omission it is guarding against: a branch that
    # names `suggest_next_unlock` with no tech.json produces a decline the player cannot
    # act on. `tests/test_progression.py` asserts this reaches BOTH stubs, because the
    # counter branch shipped dark for a day by being wired into one caller and not the
    # other.
    tech_available = _has_dataset(kb.game_version, "tech.json")
    bases_available = kb.base_radius is not None
    grounds = _corpus_probe(kb.game_version)

    fast = StubRouter(kb.lexicon, locatable, cues=cfg.cues,
                      counters=bool(counterable), counterable=counterable,
                      progression=tech_available, base_sites=bases_available,
                      corpus=grounds)
    backstop = StubRouter(kb.lexicon, locatable, cues="wide",
                          resource_floor=BACKSTOP_CONFIDENT,
                          counters=bool(counterable), counterable=counterable,
                          progression=tech_available, base_sites=bases_available,
                          corpus=grounds)

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
                return wrapped(routing_gemini.GeminiRouter(
                    kb.lexicon, locatable, unified=cfg.unified,
                    items=sorted(kb.item_sources)))
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

        # 3. Dispatch, once or twice. A chained call answers a question that is genuinely
        #    two questions - "where can I find something to beat Anubis" is a location
        #    question and a counter question, and choosing one gambles the TIER rather
        #    than the fact.
        outcome = self._dispatch(call, utterance, state, who, candidates)
        if call.then is None or isinstance(outcome.call, Decline):
            return outcome

        second = self._dispatch(call.then, utterance, state, who, candidates,
                                remember=False)
        return self._merge(outcome, second)

    def _merge(self, primary: Outcome, secondary: Outcome) -> Outcome:
        """Two answers on one message, or the primary alone.

        The secondary is dropped rather than shown whenever it did not answer: a
        decline card sitting beside a good answer reads as though part of the question
        failed, when in fact the part worth answering was answered. The cap is honoured
        too - past MAX_CARDS the extra answer stops being a second opinion and becomes
        a wall - and the primary wins, because it is the branch the cue actually led with.
        """
        if isinstance(secondary.call, Decline) or not secondary.cards:
            return primary
        if len(primary.cards) + len(secondary.cards) > MAX_CARDS:
            return primary

        draws = [o.illustrate for o in (primary, secondary) if o.illustrate is not None]

        def draw_all() -> None:
            for one in draws:
                one()

        return Outcome(primary.cards + secondary.cards, primary.call,
                       primary.candidates,
                       illustrate=draw_all if draws else None)

    def _dispatch(self, call: ToolCall, utterance: str, state: PlayerState,
                  who: str, candidates: list[Candidate],
                  remember: bool = True) -> Outcome:
        """Run one tool. Player state is injected here, never parsed from the utterance -
        "nearest" must resolve against where the player actually is.

        `remember` is off for a chained second call. Conversation memory holds one
        referent per turn, so storing both would make "what about the alpha?" ambiguous
        in exactly the way [ADR-0013](../Docs/adr/0013-conversation-memory.md) exists to
        prevent - the follow-up would resolve against whichever call happened to be
        stored last rather than against the question the player led with.
        """
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
                           f"{len(result.nodes)} of {result.total_available} clusters",
                           enabled=remember)
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
                           f"{kind or 'normal'} spawns", enabled=remember)
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

            # One card per Pal the question named, falling back to the variant family
            # when it named one. "What do I get from Astralym and Mycora" is two answers,
            # and the single-slot schema used to make it one.
            named = call.args.get("pals") or []
            subjects = named if len(named) > 1 else self.kb.lexicon.family(pal)

            self._remember(who, call, {"pal": pal}, "drops", enabled=remember)
            return Outcome(self._cards_for(subjects, render_drops), call, candidates)

        if call.name == "find_item_source":
            item = call.args.get("item")
            if not item:
                log.warning("router called %s with no item: %s", call.name, call.args)
                return self._decline(Decline(reason="no item identified"), candidates)
            result = execution.find_item_source(self.kb, item)
            log.info("find_item_source(%s) -> %d sources", item, result.total)
            self._remember(who, call, {"item": item}, f"{result.total} sources",
                           enabled=remember)
            return Outcome([cards.item_source_card(result)], call, candidates)

        if call.name == "get_pal_info":
            pal = call.args.get("pal")
            if not pal:
                log.warning("router called %s with no pal: %s", call.name, call.args)
                return self._decline(Decline(reason="no Pal identified"), candidates)

            def render_info(name: str) -> Card:
                result = execution.get_pal_info(self.kb, name)
                log.info("get_pal_info(%s) -> known=%s, %d work, %d drops",
                         name, result.known, len(result.work), result.drops)
                card = cards.pal_info_card(result, self.kb.job_label)
                if self.artwork is not None and self.artwork.icons:
                    # The icon only. This card describes a creature, not a place.
                    card.thumbnail = self.artwork.assets.icon(name)
                return card

            self._remember(who, call, {"pal": pal}, "info", enabled=remember)
            # A Paldeck slot with a variant has two answers here for the same reason it
            # does on a spawn card: Menasting and Menasting Terra have different elements
            # and different work levels, so one card would be wrong half the time.
            return Outcome(self._cards_for(self.kb.lexicon.family(pal), render_info),
                           call, candidates)

        if call.name == "find_pals_by_attribute":
            args = {k: v for k, v in call.args.items()
                    if k in ("element", "work", "level", "medium", "player_level")
                    and v is not None}
            # `mount` and `unowned` are flags rather than filters, so they are read
            # separately - an absent one is False, not "no filter given".
            mounts_only = bool(call.args.get("mount"))
            unowned = bool(call.args.get("unowned"))
            if mounts_only:
                args["mounts_only"] = True
                args["unowned_only"] = unowned
                # The roster, when it has been read. None stays None all the way to the
                # card, which says it has not looked rather than claiming you own none.
                args["owned"] = state.owned_species
            if not args:
                # Every filter empty means the model chose the class and described
                # nothing, which would return the whole Paldeck sorted by level. Same
                # shape as the missing-argument declines above, and the same answer.
                log.warning("router called %s with no filters: %s", call.name, call.args)
                return self._decline(
                    Decline(reason="I didn't catch what kind of Pal you're after"),
                    candidates)
            if not self.kb.attributes:
                return self._decline(
                    Decline(reason="I don't have work and element data loaded"),
                    candidates)

            result = execution.find_pals_by_attribute(self.kb, **args)
            log.info("find_pals_by_attribute(%s) -> %d/%d%s", args,
                     len(result.matches), result.total_available,
                     "" if result.level_exact else " (widened)")
            # Deliberately NOT remembered. Conversation memory holds one referent per
            # turn and this class produces five, so "what about the alpha?" after it has
            # no single thing to resolve against - and picking the first would answer
            # about whichever Pal happened to sort highest. ADR-0013's failure mode
            # exactly. A follow-up naming one of them resolves on its own name.
            return Outcome([cards.attribute_card(result)], call, candidates)

        if call.name == "plan_counters":
            boss = call.args.get("boss")
            if not boss:
                log.warning("router called %s with no boss: %s", call.name, call.args)
                return self._decline(Decline(reason="no boss identified"), candidates)

            from . import counters
            try:
                result = counters.plan(boss, state.owned_species)
            except counters.CounterError as e:
                # A Pal with no boss form, or a boss with no element at all - seven of
                # them have none. Declining is the honest answer; returning an empty
                # shortlist would read as "nothing works", which is a claim.
                log.info("plan_counters(%s) declined: %s", boss, e)
                return self._decline(Decline(reason=str(e)), candidates)

            log.info("plan_counters(%s) -> %d candidates (roster %s)", boss,
                     len(result.candidates),
                     "read" if result.roster_known else "not read")
            self._remember(who, call, {"boss": result.boss_id},
                           f"{len(result.candidates)} counters", enabled=remember)
            return Outcome([cards.counter_card(result)], call, candidates)

        if call.name == "lookup_corpus":
            from . import corpus

            query = call.args.get("query") or utterance
            try:
                # The entities the ranker already resolved, so the boost uses the same
                # vocabulary the lexicon produces rather than re-matching names here.
                named = tuple(c.canonical for c in candidates[:3] if c.score >= 0.9)
                result = corpus.lookup(query, entities=named)
            except corpus.CorpusError as e:
                log.info("lookup_corpus declined: %s", e)
                return self._decline(
                    Decline(reason="I don't have the knowledge corpus loaded"),
                    candidates)
            log.info("lookup_corpus(%r) -> %d passages, best %.2f",
                     query, len(result.passages), result.best_score)
            # Not remembered. A quoted passage is not an entity, so there is nothing for
            # "what about the alpha" to resolve against.
            return Outcome([cards.corpus_card(result)], call, candidates)

        if call.name == "explain_base_criteria":
            if self.kb.base_features is None:
                return self._decline(
                    Decline(reason="I don't have the terrain and water data loaded"),
                    candidates)
            # Needs no player state at all, which makes it the one base class that
            # answers without a save. Worth noting: it is also the one that explains why
            # the other two say what they say.
            result = execution.describe_base_criteria(self.kb)
            log.info("explain_base_criteria -> %d checks, %d gaps",
                     len(result.checks), len(result.gaps))
            return Outcome([cards.base_criteria_card(result)], call, candidates)

        if call.name == "rate_base_site":
            if self.kb.base_features is None:
                return self._decline(
                    Decline(reason="I don't have the terrain and water data loaded"),
                    candidates)

            # Three readings of the same class, and they resolve to different places.
            # "Rate (185, -475)" means that coordinate; "how good is my base" means where
            # they built; "rate this spot" means where they are standing. Getting them
            # the same way round matters - a player asking about their base while
            # standing somewhere else would otherwise be rated on the wrong ground and
            # never know.
            #
            # A stated coordinate wins over both, because it is the only one of the three
            # the player said out loud. It is read off the UTTERANCE rather than out of
            # the call's arguments, so it cannot have come from a model whichever router
            # claimed this - see `coordinates`.
            named = coordinates(utterance)
            want_base = bool(call.args.get("own_base")) and named is None
            if named is not None:
                spots = [(named[0], named[1], f"({named[0]:.0f}, {named[1]:.0f})")]
            elif want_base:
                if state.base_camps is None:
                    return self._decline(
                        Decline(reason="I haven't read your save, so I don't know "
                                       "where your bases are"), candidates)
                if not state.base_camps:
                    return self._decline(
                        Decline(reason="your save doesn't have any base camps in it"),
                        candidates)
                # **Nearest first when we know where they are.** `MAX_CARDS` is 2 and the
                # order was the save's, so on 2026-08-12 a player standing 0.5 units
                # inside base 3 was shown bases 1 and 2 - both over a thousand units away
                # - and told "1 more base not shown". The card had the player's position
                # and each base's position on it and used neither to choose.
                #
                # "Rate my base" said while inside one means THAT one. Numbering follows
                # the same order so the label matches what is shown, rather than printing
                # "Your base 3" first and reading as a skipped list.
                camps = list(state.base_camps)
                if state.player_coords is not None:
                    px, py = state.player_coords
                    camps.sort(key=lambda c: math.dist((px, py), c))
                spots = [(x, y, "Your base" if len(camps) == 1 else f"Your base {i}")
                         for i, (x, y) in enumerate(camps[:MAX_CARDS], 1)]
            elif state.player_coords is not None:
                spots = [(*state.player_coords, "Where you're standing")]
            else:
                return self._decline(
                    Decline(reason="I don't know where you are - read your save, or "
                                   "give me a coordinate like (185, -475)"), candidates)

            # What the base is for. Filtered to what actually has nodes, the same way
            # `suggest_base_sites` does, so a model naming crude oil cannot ask for a
            # count of something that is not placed anywhere.
            locatable = {n.resource for n in self.kb.nodes}
            wanted = [r for r in (call.args.get("resources") or []) if r in locatable]

            built = []
            for x, y, label in spots:
                rating = execution.rate_base_site(self.kb, x, y, label=label,
                                                  resources=wanted)
                log.info("rate_base_site(%.0f, %.0f) -> %d/%d criteria",
                         x, y, rating.score, rating.checkable)
                built.append(cards.base_rating_card(rating))
            # MAX_CARDS truncates silently otherwise, and a player with three bases would
            # be shown two and told nothing - which reads as "you have two bases".
            # Only when they ASKED about their bases: computed from `state.base_camps`
            # unconditionally, this line appeared on the "where you're standing" card and
            # claimed two more of something the card was not about.
            extra = (len(state.base_camps or ()) - len(built)) if want_base else 0
            if extra > 0:
                built[-1].lines.append(
                    f"_...and {extra} more base{'s' if extra != 1 else ''} not shown._")
            return Outcome(built, call, candidates)

        if call.name == "suggest_base_sites":
            wanted = [r for r in (call.args.get("resources") or [])
                      if r in {n.resource for n in self.kb.nodes}]
            if not wanted:
                log.warning("router called %s with no locatable resource: %s",
                            call.name, call.args)
                return self._decline(
                    Decline(reason="I didn't catch what the base is for",
                            known_options=sorted({n.resource for n in self.kb.nodes})),
                    candidates)
            if self.kb.base_radius is None:
                # A radius IS the question this class asks. Guessing one would put a
                # coordinate on a card backed by nothing.
                return self._decline(
                    Decline(reason="I don't know how big a base is - "
                                   "base_camp.json isn't built"),
                    candidates)

            result = execution.suggest_base_sites(
                self.kb, wanted, near=state.player_coords)
            log.info("suggest_base_sites(%s) -> %d shown, %d of %d spots complete",
                     wanted, len(result.sites), result.complete_sites,
                     result.considered)
            # Not remembered, and for the attribute-search reason: the answer is three
            # coordinates, and ADR-0013's memory holds one referent per turn.
            card = cards.base_site_card(result)
            draw = None
            if self.artwork is not None and result.sites:
                # A place, so it gets a map crop like every other coordinate card. A site
                # belongs to no single resource - it exists because several are in range -
                # so the points are labelled with what the base is FOR.
                label = "+".join(result.resources)
                draw = self.artwork.illustrate_sites(
                    card, [(s.map_x, s.map_y, label) for s in result.sites],
                    state.player_coords)
            return Outcome([card], call, candidates, illustrate=draw)

        if call.name == "find_technology":
            from . import progression

            # The fast path resolves the name and passes an id; a model passes the words
            # it heard, VERBATIM, and the resolving happens here. Same shape as a counter
            # target: the constrained lookup belongs on this side of the boundary, and it
            # is what lets 588 ordinary-English names be matched with no enum on the wire.
            try:
                tech = progression.load().get(call.args.get("tech_id") or "")
                score = 1.0
                if tech is None and call.args.get("technology"):
                    found = progression.find(call.args["technology"])
                    tech, score = found if found else (None, 0.0)
            except progression.ProgressionError:
                return self._decline(
                    Decline(reason="I don't have the technology table loaded"),
                    candidates)
            if tech is None:
                return self._decline(
                    Decline(reason="I don't know that technology"), candidates)

            state_tech = state.tech or progression.PlayerTech()
            unlocked = tech.tech_id in (state_tech.unlocked or frozenset())
            reqs = progression.requirements(tech, state_tech)
            log.info("find_technology(%s) -> unlocked=%s, %d requirements",
                     tech.tech_id, unlocked, len(reqs))
            self._remember(who, call, {"technology": tech.name}, "requirements",
                           enabled=remember)
            return Outcome([cards.technology_card(tech, reqs, unlocked, score)],
                           call, candidates)

        if call.name == "suggest_next_unlock":
            from . import progression

            goal = call.args.get("goal")
            # Unlike every other class here, this one needs NO argument: "what should I
            # research next" is complete on its own. So there is no missing-argument
            # decline - the empty call is the common case, not a model failure.
            try:
                result = progression.plan(
                    state.tech or progression.PlayerTech(),
                    goal=goal,
                    player_level=call.args.get("player_level"),
                    currency=call.args.get("currency"),
                )
            except progression.ProgressionError as e:
                # tech.json absent. Every other class still answers; this one says why.
                log.info("suggest_next_unlock declined: %s", e)
                return self._decline(
                    Decline(reason="I don't have the technology table loaded"),
                    candidates)

            log.info("suggest_next_unlock(goal=%s, level=%s) -> %d of %d locked "
                     "(%d researchable now)", goal, result.level, len(result.candidates),
                     result.total_locked, len(result.researchable))
            # Deliberately NOT remembered, for the same reason attribute search is not:
            # this class produces five referents and ADR-0013's memory holds one, so
            # "what about the alpha" after it would resolve against whichever sorted
            # first. A follow-up naming one technology resolves on its own name.
            return Outcome([cards.progression_card(result)], call, candidates)

        # A tool the router knows about but the dispatcher does not is a wiring bug.
        # Fail loudly here rather than rendering something plausible.
        raise RuntimeError(f"router produced unregistered tool: {call.name!r}")

    def _remember(self, who: str, call: ToolCall, entities: dict[str, str],
                  summary: str, enabled: bool = True) -> None:
        """Record an ANSWERED turn. Declines are deliberately not stored.

        A decline resolved nothing, so it has no referent to offer a follow-up - and
        storing its best-guess candidate would manufacture one, which is precisely the
        failure ADR-0013 warns about. After a decline, "what about the alpha" correctly
        reaches back past it to the last real answer, or asks for restatement.

        `enabled` is False for the second half of a chained call. Memory holds one
        referent per turn, so storing both would leave "what about the alpha?" resolving
        against whichever ran last rather than against what the player led with.
        """
        if not enabled:
            return
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
