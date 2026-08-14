"""Discord adapter — a thin layer over Pipeline.

Deliberately thin. All understanding and answering lives in Pipeline, so the bot adds
only transport. Text and voice converge on the same `Pipeline.handle`
([ADR-0012](../Docs/adr/0012-dual-input-channels.md)); voice adds wake-word detection and
transcription in front of it and nothing else.

**Two threads, and the boundary is the only hard part here.** Audio is captured on
sounddevice's own thread, while everything that talks to Discord must run on the asyncio
loop. Transcription is a third problem again: ~200ms of GPU work that must not block
either. So the voice path is: audio thread detects and buffers ->
`run_coroutine_threadsafe` hands the closed utterance to the loop -> the loop pushes STT
and routing into an executor -> the result is posted from the loop. Getting this wrong
does not crash; it stalls capture and the bot goes quietly deaf.

Voice input is the local microphone rather than a Discord voice channel. Discord's DAVE
encryption broke reception in py-cord (Pycord-Development/pycord#3139): the connection
succeeds, the sink attaches, and no audio ever arrives. Output is still a Discord
channel - only the input moved.

    python -m palintel.bot
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from . import activation, identity
from .activity import ActivityLog
from .capture import DEFAULT_ROOT as SESSIONS_ROOT
from .capture import UNEXPECTED, SessionCapture, Utterance
from .artwork import Artwork
from .cards import TIER_DECLINE, Card, recent_card, status_card
from . import spend as spend_mod
from .config import Config, ConfigError
from .knowledge import KnowledgeBase
from .pipeline import Pipeline, PlayerState, build_router
from .sinks import DiscordSink, LocalSink, OutputSink, Posted, to_embed
from .tools import Decline

log = logging.getLogger("palintel.bot")

try:
    import discord
except ImportError:  # pragma: no cover
    discord = None


# `/palintel iam <name>`, with or without the slash. Anchored so it cannot swallow a
# question that merely contains the phrase.
_IAM = re.compile(r"^/?palintel\s+i\s*am\s+(.+)$", re.I)


def _speaker_name(speaker) -> str:
    """A name for whoever spoke, and never a Python repr.

    `who` is not a log line - it keys conversation memory, the spend ledger and the
    capture corpus, all of which are read back later by a human or by the alias harvester.
    A `<Object id=...>` in any of them is noise that outlives the session that made it.
    """
    name = getattr(speaker, "display_name", None) or getattr(speaker, "name", None)
    if name:
        return str(name)
    uid = getattr(speaker, "id", None)
    return f"speaker {uid}" if uid is not None else "voice"


async def resolve_speaker(client, speaker, cache: dict) -> str:
    """`_speaker_name`, with a REST lookup behind it. Call this from the event loop.

    `DiscordListener._resolve` runs on py-cord's decoder thread and so can only read the
    member CACHE - which is empty without the privileged members intent, and stays empty.
    That is why 10 queries on 2026-08-13 were attributed to `speaker 366300806208552972`:
    the fallback worked exactly as designed, and the lookup it was protecting could never
    have succeeded.

    **`fetch_member`/`fetch_user` are plain REST calls and need no privileged intent.**
    Enabling `intents.members` would have fixed the cache instead, at the cost of a bot
    that refuses to LOG IN until someone flips a matching switch in the developer portal -
    trading a cosmetic failure for a total one, to populate a field in a ledger.

    Cached per process because the answer does not change within a session and this is on
    the path of every utterance. Misses are cached too: a uid that will not resolve is a
    left guild or a deleted account, and retrying it once per sentence is a stall on the
    voice path in exchange for the same answer.
    """
    name = getattr(speaker, "display_name", None) or getattr(speaker, "name", None)
    if name:
        return str(name)
    uid = getattr(speaker, "id", None)
    if uid is None:
        return "voice"
    if uid in cache:
        return cache[uid]

    # The guild's nickname first, because that is the name the text path uses and the two
    # have to agree or one person becomes two in the corpus. `fetch_user` is the fallback:
    # it always resolves for a live account but returns the global name, which may differ
    # from the nickname shown in the server.
    for source in (_guild_of(client, speaker), client):
        if source is None:
            continue
        get = getattr(source, "fetch_member", None) or getattr(source, "fetch_user", None)
        if get is None:
            continue
        try:
            found = await get(uid)
        except Exception as e:
            # Not exceptional: a member who left the guild 404s here, and the next source
            # is expected to answer. Debug so a persistent miss is still diagnosable.
            log.debug("could not fetch %s from %r: %s", uid, source, e)
            continue
        name = getattr(found, "display_name", None) or getattr(found, "name", None)
        if name:
            cache[uid] = str(name)
            log.info("resolved speaker %s to %r over REST", uid, cache[uid])
            return cache[uid]

    cache[uid] = _speaker_name(speaker)
    return cache[uid]


def _guild_of(client, speaker):
    """The guild to look `speaker` up in, or None.

    Off the speaker when py-cord gave a real member, otherwise the guild of the voice
    channel the bot is sitting in - which is the only one it can be hearing from.
    """
    guild = getattr(speaker, "guild", None)
    if guild is not None:
        return guild
    for vc in getattr(client, "voice_clients", ()) or ():
        found = getattr(getattr(vc, "channel", None), "guild", None)
        if found is not None:
            return found
    return None


def _world_id(watcher) -> str:
    """Which world bindings are scoped to. Empty when no save is being read.

    A configured `save_dir` still gets an id - the directory name - so a bot pinned to one
    world and a bot following the active one scope their bindings the same way. The
    collision this prevents is real and not hypothetical: `PlayerUId`
    `00000000-…-0001` is the host in *every* world on this machine.
    """
    if watcher is None:
        return ""
    if getattr(watcher, "world", None) is not None:
        return watcher.world.world_id
    return watcher.save_dir.name if watcher.save_dir else ""


def identity_card(players: dict[str, str], bindings, asker: str | None = None,
                  world: str = "") -> Card:
    """Who the save knows, and who has claimed whom. `/palintel who`.

    Exists because the alternative is asking people to type a Guid. The save names its
    players - `Rui`, `OutofLuck` on the measured co-op world - so binding can be done
    against a name the player recognises and can verify.
    """
    if not players:
        return Card(
            title="I don't know who's in this world",
            lines=["I haven't read `Level.sav` yet, or it holds no player entries.",
                   "", "_Without it I can't tell two players apart, so everyone gets "
                   "world-scoped answers._"],
            colour=TIER_DECLINE)

    lines = []
    for uid, name in sorted(players.items(), key=lambda kv: kv[1] or kv[0]):
        bound = bindings.user_for(uid, world) if bindings else None
        label = f"**{name or '(unnamed)'}**"
        if bound is None:
            lines.append(f"{label} — _nobody has claimed this player_")
        elif asker is not None and bound.user_id == asker:
            lines.append(f"{label} — **you**")
        else:
            lines.append(f"{label} — {bound.display_name or bound.user_id}")

    if len(players) == 1:
        # The unambiguous case. Say so plainly, because otherwise the absence of a
        # binding reads as something being wrong.
        lines += ["", "_One player in this world, so there's nothing to be ambiguous "
                  "about — everyone gets this player's state without binding._"]
    else:
        lines += ["", "_Say `/palintel iam <name>` so I answer **you** about **your** "
                  "game. Until you do, I'll answer about the world and say so._"]
    return Card(title="Players in this world", lines=lines, colour=TIER_DECLINE)


def _bind(bindings, watcher, user_id: str, display_name: str, wanted: str,
          world: str) -> Card:
    """Claim an in-game player for a Discord user. `/palintel iam <name>`.

    Matched case-insensitively against the game's own nicknames, and **a name that is not
    in the save is refused rather than stored**: a binding to a player who does not exist
    resolves to None on every query afterwards, which presents as the bot silently
    forgetting you.
    """
    players = watcher.players if watcher else {}
    if not players:
        return Card(title="I haven't read the save yet",
                    lines=["I don't know who's in this world, so I can't match a name.",
                           "", "_Try `/palintel who` in a minute._"],
                    colour=TIER_DECLINE)
    hit = [(u, n) for u, n in players.items() if n.lower() == wanted.lower()]
    if not hit:
        known = ", ".join(f"`{n}`" for n in sorted(players.values()) if n) or "(unnamed)"
        return Card(
            title=f"No player called {wanted!r}",
            lines=[f"This world has: {known}.",
                   "", "_Names come from the game, not from Discord._"],
            colour=TIER_DECLINE)
    if len(hit) > 1:
        # Two players with the same in-game name. Refuse rather than pick: binding the
        # wrong one is silent and produces confidently-wrong cards forever after.
        return Card(title=f"Two players are called {wanted!r}",
                    lines=["I can't tell which one you are, and guessing would attach "
                           "you to someone else's game."],
                    colour=TIER_DECLINE)
    uid, name = hit[0]
    if bindings is None:
        return Card(title="Identity isn't available",
                    lines=["The binding store didn't load — check the startup log."],
                    colour=TIER_DECLINE)
    taken = bindings.user_for(uid, world)
    if taken is not None and taken.user_id != user_id:
        return Card(title=f"{name} is already claimed",
                    lines=[f"{taken.display_name or taken.user_id} is bound to {name}.",
                           "", "_If that's wrong, they can rebind and free it._"],
                    colour=TIER_DECLINE)
    bindings.bind(user_id, uid, world, display_name=display_name, nickname=name)
    return Card(title=f"You're {name}",
                lines=[f"I'll answer **{display_name}** about **{name}**'s game — "
                       f"position, technologies and points.",
                       "", "_Say `/palintel who` to see everyone._"],
                colour=TIER_DECLINE)


def _build_watcher(cfg: Config):
    """The save watcher, or None if there is nothing to watch.

    Returning None rather than raising is the point: without a save the bot still answers
    every question, it just ranks by cluster size instead of distance. A missing or
    misconfigured save path degrades one word - "nearest" - and must not prevent startup.
    """
    from .saves import SaveWatcher, active_world

    if cfg.save_dir is None:
        # **Follow whichever world is being played.** The save root is derivable on
        # Windows (`%LOCALAPPDATA%/Pal/Saved/SaveGames/<steam id>/`) and every world names
        # itself in `LevelMeta.sav`, so the pick can be shown rather than made silently -
        # which is what makes a heuristic acceptable here. Setting `game.save_dir` pins
        # one world and skips all of this.
        found = active_world()
        if found is None:
            log.info("no Palworld save found and no game.save_dir set: "
                     "'nearest' will rank by cluster size")
            return None
        w = SaveWatcher(None)
        w.poll()
        log.info("save: %s", w.describe())
        return w
    if not (cfg.save_dir / "Players").is_dir():
        log.warning("game.save_dir has no Players/ directory: %s - "
                    "'nearest' will rank by cluster size", cfg.save_dir)
        return None

    from .saves import SaveWatcher
    w = SaveWatcher(cfg.save_dir)
    w.poll()          # one read now, so the first query does not have to wait for a tick
    log.info("save: %s", w.describe())
    return w


def _voice_status(cfg: Config, mic) -> str:
    """One line describing where voice actually stands.

    Distinguishes "off by configuration" from "configured on and not running", which are
    the same silence to a player and completely different problems to fix.
    """
    if not cfg.voice.enabled:
        return "off (voice.enabled = false)"
    if mic is None:
        return "**enabled but not running** - check the startup log"
    # Who the audio is attributed to belongs here rather than nowhere: it decides whose
    # conversation memory a spoken query joins, and unattributed voice silently not
    # sharing a thread with the same person's typed follow-ups is invisible otherwise.
    #
    # On the Discord source it is not a setting at all - every packet names its member -
    # so the line says that rather than reporting a `voice.speaker` the source ignores.
    heard_as = ("each speaker, from the channel" if cfg.voice.source == "discord"
                else cfg.voice.speaker or "voice (unattributed - set voice.speaker)")
    line = (f"{mic.device_name} - {'+'.join(mic.wake_names)} "
            f"@ {cfg.voice.threshold:g}, heard as {heard_as}")
    # Receive counters, Discord only. After the patch a corrupt frame no longer raises, so
    # a counter is the only remaining evidence of partial corruption - the failure mode
    # that sounds fine. Absent until the first packet, which is itself worth seeing.
    stats = getattr(mic, "stats", None)
    if callable(stats) and (s := stats()):
        line += (f" | rx ok {s['ok']}, failed {s['failed']}, "
                 f"opus err {s['opus_errors']}")
    return line


def _artwork_status(cfg: Config, pipe: Pipeline) -> str:
    """What the cards are actually carrying, which is not always what was asked for.

    Artwork turned on with no published assets fails soft - the bot answers text-only
    (Artwork.load). From Discord that is indistinguishable from having it switched off,
    so the two states are named separately here rather than both reading "text only".
    """
    asked = [k for k, on in (("maps", cfg.cards.maps), ("icons", cfg.cards.icons)) if on]
    if not asked:
        return "text only"
    if pipe.artwork is None:
        return f"{'+'.join(asked)} requested, **no assets** (run build_assets.py)"
    return f"{'+'.join(asked)}, {len(pipe.artwork.assets.regions)} map regions"


def build_pipeline(cfg: Config) -> Pipeline:
    kb = KnowledgeBase.load(cfg.data_version)
    artwork = Artwork.load(
        Path(__file__).resolve().parents[1] / "data" / cfg.data_version / "assets",
        maps=cfg.cards.maps, icons=cfg.cards.icons)
    return Pipeline(kb, build_router(kb, router_config=cfg.router), artwork=artwork)


async def _answer(channel, pipe: Pipeline, text: str, who: str,
                  activity: ActivityLog | None = None, watcher=None,
                  started: float | None = None, channel_kind: str = "text",
                  capture: SessionCapture | None = None, uid: str | None = None,
                  feedback: bool = False,
                  spend: "spend_mod.SpendLog | None" = None,
                  user_id: str | None = None,
                  bindings: "identity.Bindings | None" = None,
                  sink: "OutputSink | None" = None) -> None:
    """Route `text` and post the cards. Shared by the text and voice paths.

    Routing is a network call and transcription is GPU work, so both run in the default
    executor: doing them inline would block the event loop and, with voice, stall the
    audio receive path behind an answer nobody is waiting on yet.

    `started` is when the player began waiting - end of speech for voice, message arrival
    for text - so the total covers everything they experience, including the Discord round
    trip. Passed in rather than taken here because the two paths start the clock in
    different places and only the caller knows where.

    `who` is also the conversation-memory key (ADR-0013). The voice path passes the
    literal "voice" rather than a speaker id, because the local microphone cannot tell
    two people apart - so voice follow-ups are shared by whoever is at the mic, and a
    Discord user's typed follow-ups are their own. That is a limitation of the input, not
    a choice: see the multi-speaker item in Phase 2.

    `sink` is where the answer is delivered - `DiscordSink(channel, capture)` when not
    given, which is every Discord call site (`on_message`, the voice path); `channel` is
    unused when a caller passes its own `sink` instead, which is how local-mode queries
    reach here (see `sinks.py` and the inbox poll loop).
    """
    # Constructed up front, not lazily at the post() call - `sink.stage(...)` fires
    # before routing even starts, and DiscordSink's own stage() is a no-op, so building
    # it early costs Discord callers nothing.
    if sink is None:
        sink = DiscordSink(channel, capture)

    # Read at answer time, not cached at startup: the player moves, and "nearest" is
    # only worth answering against where they are now. The roster and the technology
    # state come off the watcher's own slower cadence - both are already-parsed values
    # here, so reading them costs an attribute access, not a parse.
    #
    # **`owned_species` was absent from this call until Phase 4**, which meant every
    # counter card in the 2026-08-11 play session said "I haven't read your Pals" while
    # `saves.owned_species` sat working and unreferenced. The roster was built, tested
    # and never connected - the same failure shape as the counter fast path being dark
    # for a day, and worth naming here rather than only in STATUS.
    #
    # **Whose state, decided per speaker.** Until 2026-08-13 this built one `PlayerState`
    # from whichever save the game wrote last, so in a co-op world every question was
    # answered as whoever had most recently autosaved. On the measured two-player world
    # that is a 35-technology tree against a 61-technology one, and a card with nothing
    # about it that looks wrong. `identity.resolve` returns None rather than guessing, and
    # `state_for(None)` is an empty state that every card already knows how to answer from.
    if watcher is None:
        state, why = PlayerState(), "no save configured"
    else:
        player_uid, why = identity.resolve(bindings, user_id, watcher.players,
                                           world=_world_id(watcher))
        state = watcher.state_for(player_uid)
    if state.tech is None and watcher is not None:
        # Worth a line in the log: an unbound speaker in a co-op world gets noticeably
        # weaker answers, and "why did it stop knowing where I am" should be answerable
        # without a debugger.
        log.info("no player state for %s (%s)", who, why)

    loop = asyncio.get_running_loop()
    # The one live progress signal this project makes (see ADR-0018): not token
    # streaming, which would misrepresent a computed answer as a generated one, but an
    # honest "the router call is in flight" marker for a medium that can show it.
    await sink.stage(uid, "routing_started")
    t_route = time.monotonic()
    try:
        outcome = await loop.run_in_executor(None, pipe.handle, text, state, who)
    except Exception:
        # Never leave a query unanswered: silence is indistinguishable from the bot
        # being down, and the player is mid-game and cannot investigate.
        log.exception("pipeline failed on %r", text)
        if activity is not None:
            activity.record("failed", text[:80])
        # Through `sink`, not `channel.send` - a `Card` renders on any medium, where a
        # raw `discord.Embed` only ever worked for one. This used to be Discord-only and
        # was the one delivery path this refactor had not yet closed.
        try:
            await sink.post([Card(title="Something broke",
                                  lines=["That query hit an internal error. It's "
                                        "logged."],
                                  colour=0xC62828)], feedback=False)
        except Exception:
            log.exception("could not even post the failure card for %r", text)
        return

    route_ms = (time.monotonic() - t_route) * 1000
    declined = isinstance(outcome.call, Decline)
    if activity is not None:
        activity.timed("route", route_ms, text[:60], who=who)
    kind = "decline" if declined else outcome.call.name
    # **Which path answered, from whether a model call happened - not from the rationale
    # text.** The old test was `"cue" in outcome.call.rationale`, which is a string sniff
    # over prose no branch is obliged to write: `_tech_named_call` says "named technology
    # 'Breed Farm' at 0.98", so all five technology lookups in the 2026-08-12 session were
    # answered by the stub and logged as model calls - in both the capture log and the
    # spend ledger. Usage is None exactly when nothing was called, which is the fact both
    # of them wanted in the first place.
    #
    # **Read off the OUTCOME, never off `pipe.router`.** The router is one object shared by
    # every caller and `pipe.handle` runs in an executor with several workers, so a
    # `pipe.router.last_usage` read here happens after other queries may have entered and
    # cleared it. Two overlapping questions - one voice, one typed - would swap costs, or
    # log a real model call as a $0 fast-path row. That is the 2026-08-12 ledger bug
    # arriving a second time by a different mechanism, and it is why the usage now travels
    # on the answer instead of sitting in a slot anyone can read.
    usage = outcome.usage
    answered_by = "model" if usage is not None else "fast"
    if capture is not None and uid:
        # What the SYSTEM decided, written as label "auto". Never as truth: labels taken
        # from the router's own behaviour are self-confirming, so a consistent bug would
        # be ratified by the corpus it produces. Only a human or a successful rephrase
        # promotes a label past auto.
        top = outcome.candidates[0] if outcome.candidates else None
        args = {} if declined else outcome.call.args
        capture.record(Utterance(
            # **Empty on the text channel, because there is no clip.** Naming a .wav that
            # was never written would put a file reference in the corpus that resolves to
            # nothing, and the STT scorers take their file list from the directory rather
            # than this field - so the lie would sit there unread until something trusted
            # it. `heard` is exact on this path: it is what the player typed, not what a
            # recogniser guessed, which is the whole reason to type the test plan.
            uid=uid, wav="" if channel_kind == "text" else f"{uid}.wav",
            seconds=0.0, heard=text,
            path="decline" if declined else answered_by,
            tool=None if declined else outcome.call.name,
            entity=(args.get("pal") or args.get("boss") or args.get("resource")
                    or args.get("item")),
            score=round(top.score, 3) if top else None,
            outcome="declined" if declined else "answered",
            # What it came closest to, captured whether or not it was acted on - see
            # `Utterance.near`'s own note on why this is a separate field from `entity`
            # rather than a fallback value for it.
            near=top.canonical if top else None,
            # The router's own named culprit, when it named one - see `Utterance
            # .unrecognized`'s own note. `Decline` carries this on every path; most
            # routers still leave it unset, and that is a fact about them, not about
            # capture.
            unrecognized=(outcome.call.unrecognized if declined else None),
            # Without this a party session is one corpus with several voices in it, and
            # two people asking similar questions look exactly like one person rephrasing
            # - which is the shape the alias harvester reads as a correction.
            who=who))
    if spend is not None:
        # **After routing, before posting**, so a Discord failure cannot lose the charge -
        # the money is spent the moment the router returns, whatever happens to the card.
        #
        # `last_usage` is None on the fast path, and that logs a $0 row rather than
        # nothing: what fraction of play reaches the model at all is the same question as
        # what it costs, and answering both from one file is why every query is recorded.
        # A stub decline is a fast-path event too, and used to be logged as a model call:
        # `needs_restatement` never reaches the model by design, so charging it one is the
        # same error as charging a fast-path answer.
        spend.record(spend_mod.charge_from(
            usage, tool=kind, path=answered_by, who=who))

    log.info("%s -> %s in %.0fms (%d card%s)", who, kind, route_ms, len(outcome.cards),
             "" if len(outcome.cards) == 1 else "s")
    # One message, several embeds. A query that resolves to a base Pal and its variant
    # has two correct answers; separate messages would let channel traffic interleave
    # and break the pairing that makes them readable.
    #
    # **Delivery goes through `sink`**, built at the top of this function - see
    # `sinks.py` and Docs/local-output-design.md.
    t_post = time.monotonic()
    try:
        # Text first, artwork second. The graded promise is "here are the coordinates",
        # and a picture is not part of it - so the answer goes out on its own round trip
        # and the upload happens after the clock has stopped. Sending them together would
        # charge every illustrated query the attachment's latency and move a bar that is
        # already unmet (Docs/00-overview.md §7).
        # The controls ride in this same call when enabled, so they cost nothing on the
        # graded path - unlike reactions, which would be one round trip each.
        posted: Posted = await sink.post(
            outcome.cards, feedback=bool(feedback and capture))
        if capture is not None and uid and posted.message_id is not None:
            # After the send, never before: the join key does not exist until Discord
            # has assigned it, and nothing upstream waits for it. A sink whose own
            # record is already keyed by `uid` (Posted.message_id is None) has nothing
            # to join - see Posted's own docstring.
            capture.attach_message(uid, posted.message_id)
    except Exception:
        # A card that was built and never delivered used to be counted as "answered",
        # because the count was written before the send. That made the one failure the
        # player actually experiences - asking and getting nothing back - invisible in
        # the very status card meant to explain it.
        log.exception("could not post the answer to %r", text)
        if activity is not None:
            activity.record("undelivered", text[:80])
        return

    # Counted only once the card is actually on the channel, and timed only then too:
    # the criterion is about queries the player got an answer to.
    if activity is not None:
        activity.record("declined" if declined else "answered", text[:80])
        activity.timed("post", (time.monotonic() - t_post) * 1000)
        if started is not None:
            # Declines are timed under their own kind: graded separately from answers,
            # because they are a different promise to the player and are slow for a
            # reason the routing policy asks for. See activity.TIMED_KINDS.
            kind_timed = f"{channel_kind}_decline" if declined else channel_kind
            # Attributed, because the graded p95 is the number a party session will move
            # most: one person on a bad connection, or one asking only model-path
            # questions, drags a single population and cannot be seen inside it.
            activity.timed(kind_timed, (time.monotonic() - started) * 1000,
                           text[:60], who=who)

    if outcome.illustrate is None:
        return

    # Render and upload are timed apart, not as one "artwork" figure. They are different
    # kinds of cost with different fixes - render is local CPU measured at 8-25ms, upload
    # is a Discord round trip nobody has measured - and a combined number cannot say
    # which one moved. Same reasoning as the stage breakdown on the status card.
    t_render = time.monotonic()
    try:
        # Rendering is CPU work on a 600 px crop - small, but the event loop also drives
        # the microphone, so it goes to the executor like routing and transcription do.
        await loop.run_in_executor(None, outcome.draw)
    except Exception:
        log.exception("could not render card artwork for %r", text)
        return

    render_ms = (time.monotonic() - t_render) * 1000
    # Medium-agnostic: "is there anything to attach" is a fact about the cards, not
    # about `art_files` (a Discord-specific conversion `DiscordSink` now owns).
    if not any(c.image or c.thumbnail for c in outcome.cards):
        # Planned but nothing drawn: every point fell outside a published map region, or
        # straddled two. Logged rather than silent - from the channel it is invisible,
        # and "the map never appears" needs to be answerable without a debugger.
        log.info("no artwork for %r (no region covers the result)", text)
        return

    kb_total = sum(len(c.image or b"") for c in outcome.cards) / 1024
    if activity is not None:
        activity.timed("art_render", render_ms, f"{kb_total:.0f}KB {text[:40]}")

    t_post = time.monotonic()
    try:
        await sink.attach_artwork(posted, outcome.cards)
    except Exception:
        # Not recorded as undelivered: the answer is on the channel and the player can
        # act on it. Only the picture is missing, and the card never claimed one.
        log.exception("could not attach card artwork to %r", text)
        return

    upload_ms = (time.monotonic() - t_post) * 1000
    if activity is not None:
        activity.timed("art_post", upload_ms, f"{kb_total:.0f}KB {text[:40]}")
    log.info("artwork: rendered %.0fms, uploaded %.0fKB in %.0fms",
             render_ms, kb_total, upload_ms)


def run() -> None:
    if discord is None:
        sys.exit("py-cord not installed:  pip install -r requirements.txt")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        cfg = Config.load()
    except ConfigError as e:
        sys.exit(f"config error: {e}")

    pipe = build_pipeline(cfg)
    # One session id for capture, spend AND latency - see where `session_id` is created
    # below. Declared up here because `activity` is built before that block runs.
    session_id = time.strftime("%Y%m%d-%H%M%S")
    start_time = time.time()
    # Persisted, which it was not until 2026-08-13. The 2026-08-12 voice p95 of 6.2s
    # against a 2.5s budget - a Phase 1 exit criterion still recorded as failing - existed
    # only in a status line pasted into a chat log, because this kept a one-hour in-memory
    # window and wrote nothing. Costs persisted; latency did not.
    activity = ActivityLog(session=session_id)
    watcher = _build_watcher(cfg)
    # Who each Discord user is in the game. Loaded once and written on every bind; see
    # identity.py for why this is persisted when PlayerState never is.
    bindings = identity.Bindings()
    summary = pipe.kb.summary()
    log.info("config: %s", cfg.redacted())
    log.info("loaded: %s", summary)

    # message_content is a PRIVILEGED intent. Without it every message arrives with an
    # empty body and the bot looks broken while connecting fine - see
    # Docs/discord-setup.md step 3.
    intents = discord.Intents.default()
    intents.message_content = True
    # **`intents.members` is deliberately NOT set here.** It would populate the member
    # cache that `guild.get_member` reads, but it is privileged the same way
    # message_content is, and py-cord raises PrivilegedIntentsRequired at LOGIN when the
    # portal toggle is off - taking the whole bot down to fix names in a log field. The
    # names are fetched over REST instead; see `_speaker_name`.
    client = discord.Client(intents=intents)

    # Boxed, because a reconnect re-fires on_ready and a second polling task would double
    # the save reads for no benefit.
    tasks: dict[str, object] = {"save": None, "beat": None}

    @client.event
    async def on_ready() -> None:
        channel = client.get_channel(cfg.discord.channel_id)
        where = f"#{channel.name}" if channel else f"id {cfg.discord.channel_id} (NOT FOUND)"
        log.info("connected as %s, listening in %s", client.user, where)
        if channel is None:
            log.error("channel not visible: check the id, and that the bot was invited "
                      "to that server with permission to view the channel")
        if watcher is not None and not tasks["save"]:
            tasks["save"] = client.loop.create_task(watch_saves())
        # Started here rather than at import: a heartbeat before the gateway connects
        # would claim a bot is running while it is still deciding whether it can.
        if not tasks["beat"]:
            tasks["beat"] = client.loop.create_task(beat())

    @client.event
    async def on_message(message: "discord.Message") -> None:
        # Loop prevention. A bot replying to itself is the classic Discord failure and
        # it escalates fast, so this guard comes before anything else.
        if message.author == client.user or message.author.bot:
            return
        if message.channel.id != cfg.discord.channel_id:
            return

        text = message.content.strip()

        # Checked before listen_mode, deliberately. Status is what you reach for when the
        # bot seems not to be listening, so gating it behind the same prefix or mention
        # rules that might be the problem would make it useless in the one case it is
        # for. A plain message rather than a registered slash command: this needs no
        # command sync, no extra intent, and works the moment the bot connects.
        if text.lower() in ("/palintel status", "/palintel", "palintel status"):
            await message.channel.send(embed=to_embed(status_card(
                activity,
                voice=_voice_status(cfg, listener["mic"]),
                save=watcher.describe() if watcher else "not configured",
                roster=watcher.describe_roster() if watcher else "not configured",
                spend=(spend_mod.describe(spend, cfg.cost.balance_usd,
                                          cfg.cost.warn_below_usd)
                       + (f"\n{by}" if (by := spend_mod.describe_users(
                           spend_mod.all_charges())) else "")
                       if cfg.cost.enabled else ""),
                router=pipe.router.name,
                artwork=_artwork_status(cfg, pipe))))
            return
        if text.lower() in ("/palintel recent", "palintel recent"):
            await message.channel.send(embed=to_embed(recent_card(activity)))
            return

        # --- identity, the multi-user join -------------------------------------------
        #
        # Before the listen-mode gate for the same reason status is: someone typing these
        # is trying to work out why their answers are thin, and gating them behind the
        # rules that might be the problem makes them useless exactly when they are needed.
        if text.lower() in ("/palintel who", "palintel who"):
            await message.channel.send(embed=to_embed(identity_card(
                watcher.players if watcher else {}, bindings,
                asker=str(message.author.id), world=_world_id(watcher))))
            return
        if low_iam := _IAM.match(text):
            await message.channel.send(embed=to_embed(_bind(
                bindings, watcher, str(message.author.id),
                message.author.display_name, low_iam.group(1).strip(),
                world=_world_id(watcher))))
            return
        if text.lower() in ("/palintel reset", "palintel reset"):
            # ADR-0013's manual clear. Scoped to the asker: one person's conversation
            # going wrong is not a reason to drop everyone else's, and in a shared
            # channel a global reset would be an easy way to disrupt other people.
            who = message.author.display_name
            pipe.memory.forget(who)
            await message.channel.send(embed=to_embed(Card(
                title="Forgotten",
                lines=["I've cleared what I remembered of our conversation."],
                colour=TIER_DECLINE)))
            log.info("memory reset for %s", who)
            return

        # **The retroactive half of the feedback buttons, and the more important half.**
        # Both defects the 2026-08-12 session found were "I acted on the card and the
        # world disagreed" - a spawn card that sent the player to a level 68-72 area they
        # could not survive, and a rating card about somewhere they had not named. Neither
        # is knowable until you travel, by which point the buttons are out of view and
        # `FeedbackView` has no timeout but plenty of scrollback above it.
        #
        # **Reply to the card**, rather than pasting a link: a Discord reply already
        # carries the message id, which is the join key `record_feedback` wants, and it
        # works on a phone in the middle of a fight. Checked before the listen-mode gate
        # for the same reason status is - the thing being reported may BE the gate.
        low = text.lower()
        if low.startswith(("/palintel wrong", "palintel wrong")):
            # Cut on the lower-cased copy so "/PalIntel Wrong ..." keeps its note, and
            # slice the ORIGINAL so the note keeps its capitals.
            note = text[low.index("wrong") + len("wrong"):].strip()
            ref = getattr(message.reference, "message_id", None)
            if capture is None or ref is None:
                await message.channel.send(embed=to_embed(Card(
                    title="Reply to the card",
                    lines=["Reply to the answer that was wrong and say `/palintel wrong "
                           "<what happened>` - the reply is how I know which card you "
                           "mean."] + ([] if capture is not None else
                                       ["_Capture is off, so nothing would be recorded._"]),
                    colour=TIER_DECLINE)))
                return
            capture.record_feedback(ref, UNEXPECTED,
                                    who=message.author.display_name, note=note)
            log.info("feedback %s on message %s (reply): %r", UNEXPECTED, ref, note[:80])
            await message.channel.send(embed=to_embed(Card(
                title="Noted",
                lines=["Logged against that card - thanks."],
                colour=TIER_DECLINE)))
            return

        mode = cfg.discord.listen_mode
        if mode == "prefix":
            if not text.startswith(cfg.discord.prefix):
                return
            text = text[len(cfg.discord.prefix):].strip()
        elif mode == "mention":
            if client.user not in message.mentions:
                return
            text = text.replace(f"<@{client.user.id}>", "").strip()

        if not text:
            return

        log.info("query from %s: %r", message.author.display_name, text)
        # The clock starts here rather than at Discord's own timestamp: gateway delivery
        # is not something this process can affect, and charging it to the pipeline would
        # make the number unactionable.
        # `who` stays the display name because that is what conversation memory and the
        # spend ledger key on. `user_id` is passed separately and is what IDENTITY uses:
        # display names change mid-session and collide across servers, and either one
        # would silently split or merge a person's binding.
        # **`capture` and `feedback` belong here too, and their absence cost a session.**
        # On 2026-08-13 the test plan was deliberately run over TEXT, to take routing
        # readings without STT errors in them - and neither argument was passed, so there
        # were no feedback buttons to press and not one of those 61 queries reached
        # `log.jsonl`. The analyser saw 3 utterances in a 64-query session.
        #
        # `capture` was hoisted to bot scope precisely so the text channel could use it,
        # and then this call site was never given it. Same mistake the hoist was made to
        # fix, one level further out.
        await _answer(message.channel, pipe, text, message.author.display_name,
                      activity, watcher, started=time.monotonic(),
                      channel_kind="text", spend=spend,
                      capture=capture, uid=f"t{int(time.time() * 1000):x}",
                      feedback=cfg.capture.feedback,
                      user_id=str(message.author.id), bindings=bindings)

    async def beat() -> None:
        """Publish what only this process knows, on a timer.

        Voice state, the receive counters, the router's identity and uptime live in memory
        and died with the process; the console reported them as unavailable because they
        genuinely were. This is also how a second bot is prevented - see botstate: a
        console that cannot see a running bot will happily start another one on the same
        Discord token, and the only symptom is every question answered twice.
        """
        from . import botstate

        while True:
            try:
                mic = listener["mic"]
                stats = getattr(mic, "stats", None)
                botstate.write({
                    "started_at": start_time,
                    "uptime": time.monotonic() - activity.started,
                    "session": session_id,
                    "router": pipe.router.name,
                    "channel_id": cfg.discord.channel_id,
                    "voice": _voice_status(cfg, mic),
                    "receive": stats() if callable(stats) else None,
                    "save": watcher.describe() if watcher else "not configured",
                    "world": (watcher.world.world_id
                              if watcher and watcher.world else None),
                    "counts": activity.counts(),
                    "spend_usd": spend.total if spend else 0.0,
                })
            except Exception:
                # A heartbeat failure must never take the bot down. Its only consequence
                # is the console showing "not connected" for a bot that is answering fine.
                log.exception("heartbeat failed")
            await asyncio.sleep(botstate.BEAT_SECONDS)

    async def watch_saves() -> None:
        """Re-read the save on a timer for as long as the bot runs.

        In an executor because a poll is file IO plus a parse. It is milliseconds on a
        player save, but it sits on the same loop as voice dispatch, and a stalled loop
        there drops audio rather than merely delaying it.
        """
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(watcher.interval)
            try:
                await loop.run_in_executor(None, watcher.poll)
                # The roster read decides its own cadence - it is a multi-megabyte parse
                # against the player save's few kilobytes - so this calls it every tick
                # and `poll_roster` returns immediately until it is due.
                await loop.run_in_executor(None, watcher.poll_roster)
            except Exception:
                # poll() swallows its own failures; this catches anything above it, so a
                # bad save can never end the polling task and silently freeze "nearest"
                # at whatever position it last saw.
                log.exception("save poll failed")

    listener = {"mic": None}   # boxed so on_ready can see it across re-fires

    # `session_id` is created at the top of `run`, beside the ActivityLog that also needs
    # it. One id for the whole run, shared by capture, spend AND latency so a session's
    # clips, log, bill and timings sit in one directory. It was created here originally
    # because TYPED queries cost money too, and the first version had the spend log
    # unreachable from the text handler for exactly that reason.
    spend = spend_mod.SpendLog(session_id) if cfg.cost.enabled else None
    if spend is not None:
        log.info("spend: %s", spend_mod.describe(
            spend, cfg.cost.balance_usd, cfg.cost.warn_below_usd))

    # At bot scope for the same reason `spend` is, and it took the same mistake to learn:
    # clip capture is voice-only, but FEEDBACK is not. `/palintel wrong` arrives as a
    # reply on the text channel whichever way the question was asked, so a capture object
    # reachable only from inside `start_voice` would leave the retroactive channel dead in
    # exactly the case it exists for - a card that only turns out to be wrong later.
    capture = (SessionCapture(session=session_id) if cfg.capture.enabled else None)
    if capture is not None:
        log.info("capture: session %s -> %s", capture.session, capture.dir)

    async def start_voice() -> None:
        """Listen for speech and answer into the text channel.

        Two sources, chosen by `voice.source`, and everything after the wake word is
        identical: the local microphone, or a Discord voice channel via `pydiscorddave`.

        Discord receive was recorded as blocked on DAVE for months and was not - DAVE
        decrypts fine, py-cord 2.8's receive package was unfinished. **The mic stays the
        default anyway**, because the way Discord receive fails is by going quietly deaf,
        which ADR-0004 names as the worst kind, and a regression there should cost party
        voice rather than all voice.
        """
        from .stt import Transcriber

        text_channel = client.get_channel(cfg.discord.channel_id)
        if text_channel is None:
            log.error("cannot start voice: text channel %s not visible",
                      cfg.discord.channel_id)
            return

        # Loaded once. A per-utterance load would put seconds of model initialisation
        # into the latency of every query.
        transcriber = Transcriber(pipe.kb.lexicon)
        log.info("voice: STT on %s", transcriber.device)

        # `capture` is created at bot scope - see there. Clip capture is still voice-only,
        # because the text path has no audio to keep; it is the feedback log that both
        # channels share.

        loop = asyncio.get_running_loop()
        tmp = TemporaryDirectory(prefix="palintel-voice-")
        # uid -> name, filled by `resolve_speaker` over REST. Lives for the session: the
        # answer does not change within one, and this is on every utterance's path.
        names: dict[int, str] = {}

        async def on_speech(utt, speaker=None) -> None:
            # `speaker` is None from the microphone, which cannot say who spoke, and a
            # Discord Member from the channel, which can. Everything else is shared.
            #
            # faster-whisper reads a file; the buffer is raw PCM. A scratch WAV is
            # cheaper and far less fragile than teaching the transcriber to take bytes,
            # and these are one-to-three second clips.
            uid = f"{int(time.time() * 1000):x}"
            # **Observed when Discord can tell us, configured when only the mic can.**
            # Resolved HERE rather than just before `_answer`, because everything timed or
            # captured below belongs to a speaker too - and a party session's clips and
            # timings with no name on them are one corpus with several voices in it.
            #
            # The mic cannot say who spoke, so attribution is `voice.speaker` - naming the
            # person at the machine is what lets a spoken question be followed up in text,
            # which is what ADR-0012 promises. Unset, it stays "voice": guessing which
            # Discord user is sitting there would attribute speech to the wrong person in
            # a shared channel, and that is worse than not joining the two.
            #
            # A Discord packet carries its member, so on that source the guess disappears
            # and the promise holds for everyone in the channel rather than for one person
            # by declaration. `display_name` because that is the key the text path uses.
            #
            # **`str(speaker)` is never the answer.** py-cord hands a bare `Object` when
            # the member is not cached, and its repr is `<Object id=366300806208552972>` -
            # which is what 20 queries across four sessions on 2026-08-13 were attributed
            # to, in the memory keys, the spend ledger and the capture corpus.
            # `DiscordListener._resolve` now looks the member up first; this is the
            # fallback for when it genuinely cannot, and it says "speaker <id>" rather
            # than leaking a Python repr into data meant to be read later.
            who = (await resolve_speaker(client, speaker, names)
                   if speaker is not None else (cfg.voice.speaker or "voice"))
            # Captured or not, the WAV is written either way - faster-whisper reads a
            # file, not a buffer. Capture only changes WHERE, and whether it survives.
            path = (str(capture.write_wav(uid, utt.pcm) or f"{tmp.name}/{uid}.wav")
                    if capture is not None else f"{tmp.name}/{uid}.wav")
            if capture is None:
                with wave.open(path, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16_000)
                    w.writeframes(utt.pcm)

            t_stt = time.monotonic()
            text = await loop.run_in_executor(None, transcriber.transcribe, path)
            stt_ms = (time.monotonic() - t_stt) * 1000
            log.info("voice: heard %r (%.1fs audio, %.0fms STT, closed on %s)",
                     text, utt.seconds, stt_ms, utt.reason)
            activity.timed("stt", stt_ms, f"{utt.seconds:.1f}s audio", who=who)
            if not text.strip():
                # Wake word fired on something that was not speech. Silence is right:
                # posting "I didn't catch that" to a false trigger is channel noise for
                # a query nobody made - but it is recorded, because a pile of these is
                # the signature of a detector firing on background noise.
                activity.record("empty", f"{utt.seconds:.1f}s")
                return
            if activation.overheard(text):
                # A video playing in the room, transcribed accurately. Recorded as empty
                # rather than heard, and dropped BEFORE `activity.record("heard", ...)`
                # so it never enters the graded population, never spends a model call,
                # and - the reason this gate exists at all - never becomes a captured
                # clip with a routing label attached to it.
                activity.record("empty", f"overheard: {text[:40]!r}")
                log.info("voice: discarding overheard media audio (%r)", text[:60])
                return
            activity.record("heard", text[:80])

            act = activation.detect(text)
            if activation.bare(act):
                # The wake word arrived and the question did not - endpointing closed the
                # clip too early. Routing it costs a model call to decline something with
                # no question in it (1.4s and 1.5s, measured in play 2026-08-11), and the
                # gate sits before `_answer` precisely so those never start the graded
                # clock. This is the one class of query where not answering is strictly
                # better than answering fast.
                #
                # Confident vs marginal decides whether to say so. A confident wake match
                # means someone did address the bot and got cut off, and a silent drop
                # there is ADR-0004's worst failure mode - the player speaks, nothing
                # happens, nothing to diagnose. A marginal one is more likely party
                # chatter that sounded like the wake word, and answering it would be
                # channel noise for a query nobody made.
                activity.record("empty", f"wake word only: {text[:40]!r}")
                log.info("voice: wake word with no query (%r, score %.2f)",
                         text, act.score)
                if act.confident:
                    await text_channel.send("I caught my name but not the question.")
                return

            # On the Discord source the packet names its member, so a spoken question can
            # be attributed to a bound player exactly like a typed one - which is what
            # makes party voice and per-player state the same feature rather than two.
            # The microphone cannot say who spoke, so it passes no id and its speaker
            # falls to the unbound path unless the world has only one player.
            speaker_id = getattr(speaker, "id", None)
            await _answer(text_channel, pipe, text, who,
                          activity, watcher, spend=spend,
                          started=utt.ended_at, channel_kind="voice",
                          capture=capture, uid=uid,
                          feedback=cfg.capture.feedback,
                          user_id=str(speaker_id) if speaker_id is not None else None,
                          bindings=bindings)

        def _report(fut) -> None:
            """Surface anything on_speech raised.

            Without this the Future returned by run_coroutine_threadsafe is dropped on
            the floor, and asyncio only mentions the exception if and when the Future is
            garbage collected. Everything after transcription - posting the card
            included - could therefore fail in total silence, which presents to the
            player as a query that was heard and simply never answered.
            """
            try:
                fut.result()
            except Exception:
                log.exception("voice: answering failed after the wake word fired")
                activity.record("failed", "answer raised after transcription")

        def dispatch(utt, speaker=None) -> None:
            # Called on sounddevice's audio thread, or py-cord's decoder thread. Nothing
            # here may block or touch the loop directly, so the whole answer is handed
            # over - but not forgotten; a stall here drops audio rather than merely
            # delaying it.
            asyncio.run_coroutine_threadsafe(
                on_speech(utt, speaker), loop).add_done_callback(_report)

        if cfg.voice.source == "discord":
            from .discord_voice import DiscordListener, patch_available

            if not patch_available():
                # Refuse rather than connect. A `VoiceClient` on unpatched py-cord 2.8
                # raises inside `start_recording`, and what the player sees either way is
                # a channel the bot has joined and does not respond in. Saying so at
                # startup is the difference between a five-minute fix and an evening.
                raise RuntimeError(
                    "voice.source = 'discord' needs the receive patch:\n"
                    "  .venv\\Scripts\\python -m pip install -e C:/Projects/PyDiscordDave\n"
                    "Or set voice.source = 'mic' in config.local.toml to use the "
                    "microphone.")
            # The sink hands (speaker, utterance); the mic hands (utterance).
            listener_ = DiscordListener(
                lambda speaker, utt: dispatch(utt, speaker),
                channel_id=cfg.voice.channel_id, models=list(cfg.voice.models),
                threshold=cfg.voice.threshold, log_=activity)
            await listener_.start(client)
        else:
            from .mic import MicListener

            listener_ = MicListener(dispatch, models=list(cfg.voice.models),
                                    threshold=cfg.voice.threshold,
                                    device=cfg.voice.device, log=activity)
            listener_.start()
        listener["mic"] = listener_

    if cfg.voice.enabled:
        _orig_ready = on_ready

        @client.event
        async def on_ready() -> None:  # noqa: F811  (replaces the text-only handler)
            await _orig_ready()
            if listener["mic"] is not None:
                return          # a reconnect re-fires on_ready; one stream is enough
            try:
                await start_voice()
            except Exception:
                # Voice failing must not take the text path down with it.
                log.exception("voice startup failed - continuing text-only")

    # No log_handler kwarg here: that is discord.py's API. py-cord forwards run()'s
    # kwargs straight to start(), which rejects it. Logging is configured above instead.
    try:
        client.run(cfg.discord.token)
    finally:
        # A clean shutdown says so immediately rather than making the console wait out the
        # staleness window. Absence and staleness mean the same thing, which is what lets
        # a killed bot be handled correctly too - this is a courtesy, not the mechanism.
        from . import botstate
        botstate.clear()


# ============================================================== local (Discord-free)
#
# `run()` above is ~480 lines built entirely around `discord.Client`'s lifecycle - the
# mic/wake-word listener is even started from inside `on_ready`, a Discord CONNECTION
# EVENT, even though audio capture itself never touches Discord. Reshaping that function
# to also serve a Discord-free path would be live surgery on a working, heavily-tested
# entry point, and this project's own history is full of exactly that shape of bug
# hiding in exactly that kind of code: `counters=True` reaching one stub and not the
# other for a day, capture wired into voice and not text, party voice dark for months on
# an unchecked assumption. None of those were logic bugs in a pure function - they were
# wiring bugs in event-driven startup code with shared mutable state, which is what
# `run()` is.
#
# So this is a SEPARATE function instead, built fresh, and `main()` below is the one
# place that picks between them. Some setup duplicates what `run()` also does (the
# pipeline, the activity log, the watcher, the heartbeat) - accepted cost, not an
# oversight: extracting the shared pieces risks changing the Discord path's behaviour
# to prove something about a path that does not even exist yet. See ADR-0018 and
# Docs/local-output-design.md.
#
# **Voice input is not out of scope - it was never the thing rejected.** The decision
# recorded in ADR-0018 was against a socket the bot LISTENS on for pending queries, in
# favor of the file-based inbox below; that choice is about query delivery and can be
# revisited if the file-based approach turns out to have problems. It says nothing about
# the microphone. `voice.source = "mic"` (`mic.py`) is already Discord-independent -
# `start_voice()` inside `run()` only lives where it does because `on_ready` was a
# convenient place to start it, not because it needs anything Discord provides. So
# `_start_voice_local` below wires the same mic path in here directly.
#
# `voice.source = "discord"` is different: `DiscordListener` needs a live
# `discord.Client` to attach a receive sink to, which this process never constructs.
# That combination is rejected at config load (see `config.py`), not discovered here.


async def _start_voice_local(cfg: Config, pipe: Pipeline, activity: ActivityLog,
                             watcher, bindings: "identity.Bindings",
                             capture: SessionCapture | None,
                             spend: "spend_mod.SpendLog | None",
                             session_dir: Path) -> "object | None":
    """`start_voice()`'s Discord-free twin - mic only, everything past the wake word
    is otherwise identical: same transcriber, same activation gates, same `_answer`
    call. What differs is delivery - a fresh `LocalSink` per utterance instead of a
    channel-bound one, since there is no persistent channel to post into.

    Returns the running `MicListener` (or `None` if startup failed) so the caller can
    report its status in the heartbeat, matching `_voice_status`'s own contract.
    """
    from .stt import Transcriber
    from .mic import MicListener

    transcriber = Transcriber(pipe.kb.lexicon)
    log.info("voice: STT on %s", transcriber.device)

    loop = asyncio.get_running_loop()
    tmp = TemporaryDirectory(prefix="palintel-voice-")

    async def on_speech(utt, speaker=None) -> None:
        # `speaker` is always None here - `MicListener` never supplies one, and the
        # only source that ever would (`voice.source = "discord"`) is rejected at
        # config load before this function is reached at all.
        uid = f"{int(time.time() * 1000):x}"
        who = cfg.voice.speaker or "voice"
        path = (str(capture.write_wav(uid, utt.pcm) or f"{tmp.name}/{uid}.wav")
                if capture is not None else f"{tmp.name}/{uid}.wav")
        if capture is None:
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16_000)
                w.writeframes(utt.pcm)

        t_stt = time.monotonic()
        text = await loop.run_in_executor(None, transcriber.transcribe, path)
        stt_ms = (time.monotonic() - t_stt) * 1000
        log.info("voice: heard %r (%.1fs audio, %.0fms STT, closed on %s)",
                 text, utt.seconds, stt_ms, utt.reason)
        activity.timed("stt", stt_ms, f"{utt.seconds:.1f}s audio", who=who)
        if not text.strip():
            activity.record("empty", f"{utt.seconds:.1f}s")
            return
        if activation.overheard(text):
            activity.record("empty", f"overheard: {text[:40]!r}")
            log.info("voice: discarding overheard media audio (%r)", text[:60])
            return
        activity.record("heard", text[:80])

        act = activation.detect(text)
        if activation.bare(act):
            activity.record("empty", f"wake word only: {text[:40]!r}")
            log.info("voice: wake word with no query (%r, score %.2f)",
                     text, act.score)
            if act.confident:
                # Same ADR-0004 reasoning as the Discord path: a confident wake match
                # with nothing after it is the one failure a silent drop would hide.
                await LocalSink(session_dir, uid).post(
                    [Card(title="Didn't catch a question",
                         lines=["I caught my name but not the question."],
                         colour=TIER_DECLINE)],
                    feedback=False)
            return

        await _answer(None, pipe, text, who, activity, watcher, spend=spend,
                      started=utt.ended_at, channel_kind="voice",
                      capture=capture, uid=uid, feedback=cfg.capture.feedback,
                      user_id=None, bindings=bindings,
                      sink=LocalSink(session_dir, uid))

    def _report(fut) -> None:
        try:
            fut.result()
        except Exception:
            log.exception("voice: answering failed after the wake word fired")
            activity.record("failed", "answer raised after transcription")

    def dispatch(utt, speaker=None) -> None:
        asyncio.run_coroutine_threadsafe(
            on_speech(utt, speaker), loop).add_done_callback(_report)

    listener_ = MicListener(dispatch, models=list(cfg.voice.models),
                            threshold=cfg.voice.threshold,
                            device=cfg.voice.device, log=activity)
    listener_.start()
    return listener_


async def _poll_inbox(session_dir: Path, pipe: Pipeline, activity: ActivityLog,
                      watcher, bindings: "identity.Bindings",
                      capture: SessionCapture | None,
                      spend: "spend_mod.SpendLog | None", poll_s: float) -> None:
    """Watches `<session_dir>/inbox/` for queries the console wrote - one file per
    query, named `<uid>.json`, deleted the moment it is claimed. See
    Docs/local-output-design.md §3.1: the filename IS the queue, so there is no cursor
    or offset to maintain, unlike a shared log.
    """
    inbox = session_dir / "inbox"
    while True:
        try:
            if inbox.is_dir():
                # Sorted by write time, not filename - a `uid` is not chronological.
                pending = sorted(inbox.glob("*.json"), key=lambda p: p.stat().st_mtime)
                for path in pending:
                    await _handle_inbox_file(path, session_dir, pipe, activity,
                                             watcher, bindings, capture, spend)
        except Exception:
            # A poll failure must not take the loop down - the next tick tries again,
            # and a query that keeps failing is still visible in the log line below.
            log.exception("inbox poll failed")
        await asyncio.sleep(poll_s)


async def _handle_inbox_file(path: Path, session_dir: Path, pipe: Pipeline,
                             activity: ActivityLog, watcher,
                             bindings: "identity.Bindings",
                             capture: SessionCapture | None,
                             spend: "spend_mod.SpendLog | None") -> None:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("inbox: could not read %s: %s", path, e)
        path.unlink(missing_ok=True)
        return
    # Deleted BEFORE the answer is fully rendered, not after - a crash mid-answer must
    # not replay the same query forever (Docs/local-output-design.md §3.1, and its own
    # open question about the narrow window this still leaves).
    path.unlink(missing_ok=True)

    uid = str(row.get("uid") or path.stem)
    text = str(row.get("text") or "").strip()
    if not text:
        log.warning("inbox: %s carried no text, discarding", path.name)
        return

    log.info("local query %s: %r", uid, text)
    sink = LocalSink(session_dir, uid)
    sink.record_query(text, at=row.get("at"))
    # `who` is a fixed local identity, not solved per-message - one browser tab, one
    # machine, one bot. See Docs/local-output-design.md §7.
    await _answer(None, pipe, text, "local", activity, watcher,
                 started=time.monotonic(), channel_kind="text",
                 capture=capture, uid=uid, feedback=False, spend=spend,
                 user_id=None, bindings=bindings, sink=sink)


async def _beat_local(cfg: Config, session_id: str, start_time: float,
                      activity: ActivityLog, pipe: Pipeline, watcher,
                      spend: "spend_mod.SpendLog | None",
                      voice_listener: dict) -> None:
    """`beat()`'s local-mode twin - see that function's own docstring for why this
    exists at all: the console cannot see anything this process holds only in memory,
    including whether it is still alive.

    `voice_listener` is the same boxed-dict pattern `run()` uses (`listener["mic"]`) -
    `_start_voice_local` fills it in once the mic is actually running, and this reads
    whatever is there on every tick rather than freezing the value from before startup.
    """
    from . import botstate

    while True:
        try:
            botstate.write({
                "started_at": start_time,
                "uptime": time.monotonic() - activity.started,
                "session": session_id,
                "router": pipe.router.name,
                "output": "local",
                "voice": _voice_status(cfg, voice_listener["mic"]),
                "save": watcher.describe() if watcher else "not configured",
                "world": (watcher.world.world_id
                          if watcher and watcher.world else None),
                "counts": activity.counts(),
                "spend_usd": spend.total if spend else 0.0,
            })
        except Exception:
            log.exception("heartbeat failed")
        await asyncio.sleep(botstate.BEAT_SECONDS)


async def _run_local_async(cfg: Config, pipe: Pipeline, session_id: str,
                           start_time: float, activity: ActivityLog, watcher,
                           bindings: "identity.Bindings",
                           capture: SessionCapture | None,
                           spend: "spend_mod.SpendLog | None",
                           session_dir: Path) -> None:
    poll_s = cfg.output.inbox_poll_ms / 1000
    log.info("local mode: watching %s every %dms", session_dir / "inbox",
             cfg.output.inbox_poll_ms)

    voice_listener = {"mic": None}
    if cfg.voice.enabled:
        try:
            voice_listener["mic"] = await _start_voice_local(
                cfg, pipe, activity, watcher, bindings, capture, spend, session_dir)
        except Exception:
            # Voice failing must not take the text path down with it - matches
            # `run()`'s own guard around `start_voice()`.
            log.exception("voice startup failed - continuing text-only")

    # All three loop forever (or, for voice, run on their own thread); `gather`
    # returning at all means one of the two tasks below raised past its own
    # try/except, which is the signal a bug got through rather than routine.
    await asyncio.gather(
        _poll_inbox(session_dir, pipe, activity, watcher, bindings, capture, spend,
                   poll_s),
        _beat_local(cfg, session_id, start_time, activity, pipe, watcher, spend,
                   voice_listener),
    )


def run_local() -> None:
    """Entry point for `output.medium = "local"`. No `discord.Client` is ever
    constructed - see the module-level note above for why this is a separate function
    rather than a branch inside `run()`.
    """
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        cfg = Config.load()
    except ConfigError as e:
        sys.exit(f"config error: {e}")

    pipe = build_pipeline(cfg)
    session_id = time.strftime("%Y%m%d-%H%M%S")
    start_time = time.time()
    activity = ActivityLog(session=session_id)
    watcher = _build_watcher(cfg)
    bindings = identity.Bindings()
    log.info("config: %s", cfg.redacted())
    log.info("loaded: %s", pipe.kb.summary())

    # `chat.jsonl` / `inbox/` live under the session directory regardless of whether
    # gameplay CAPTURE is on - that flag is about voice-clip privacy (off by default);
    # the local Chat tab's own history is not optional the way a recorded clip is, so
    # it is not gated on the same switch.
    session_dir = SESSIONS_ROOT / session_id
    capture = SessionCapture(session=session_id) if cfg.capture.enabled else None
    spend = spend_mod.SpendLog(session_id) if cfg.cost.enabled else None

    try:
        asyncio.run(_run_local_async(cfg, pipe, session_id, start_time, activity,
                                     watcher, bindings, capture, spend, session_dir))
    finally:
        from . import botstate
        botstate.clear()


def main() -> None:
    """The one entry point `python -m palintel.bot` reaches, whether launched directly
    or by the console's `Supervisor` (same subprocess command either way). Reads config
    once to decide which medium to start, then hands off completely - `run()` and
    `run_local()` each load it again themselves, which costs a local file read and
    buys each function staying fully self-contained.
    """
    try:
        cfg = Config.load()
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    if cfg.output.medium == "local":
        run_local()
    else:
        run()


if __name__ == "__main__":
    main()
