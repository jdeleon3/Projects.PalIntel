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
import io
import logging
import re
import sys
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from . import activation, identity
from .activity import ActivityLog
from .capture import (FEEDBACK_KINDS, NOTE_LIMIT, UNEXPECTED, SessionCapture,
                      Utterance)
from .artwork import Artwork
from .cards import TIER_DECLINE, Card, recent_card, status_card
from . import spend as spend_mod
from .config import Config, ConfigError
from .knowledge import KnowledgeBase
from .pipeline import Pipeline, PlayerState, build_router
from .tools import Decline

log = logging.getLogger("palintel.bot")

try:
    import discord
except ImportError:  # pragma: no cover
    discord = None


def to_embed(card: Card, index: int = 0, *, with_art: bool = True) -> "discord.Embed":
    embed = discord.Embed(title=card.title,
                          description="\n".join(card.lines),
                          color=card.colour)
    if card.footer:
        embed.set_footer(text=card.footer)
    if with_art:
        names = card.attachments(index)
        if "image" in names:
            embed.set_image(url=f"attachment://{names['image']}")
        if "thumbnail" in names:
            embed.set_thumbnail(url=f"attachment://{names['thumbnail']}")
    return embed


def art_files(cards_: list[Card]) -> list["discord.File"]:
    """The attachments the embeds reference, in the order their cards appear."""
    files = []
    for i, card in enumerate(cards_):
        names = card.attachments(i)
        if card.image is not None:
            files.append(discord.File(io.BytesIO(card.image), filename=names["image"]))
        if card.thumbnail is not None:
            files.append(discord.File(str(card.thumbnail), filename=names["thumbnail"]))
    return files


# `/palintel iam <name>`, with or without the slash. Anchored so it cannot swallow a
# question that merely contains the phrase.
_IAM = re.compile(r"^/?palintel\s+i\s*am\s+(.+)$", re.I)


def identity_card(players: dict[str, str], bindings, asker: str | None = None) -> Card:
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
        bound = bindings.user_for(uid) if bindings else None
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


def _bind(bindings, watcher, user_id: str, display_name: str, wanted: str) -> Card:
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
    taken = bindings.user_for(uid)
    if taken is not None and taken.user_id != user_id:
        return Card(title=f"{name} is already claimed",
                    lines=[f"{taken.display_name or taken.user_id} is bound to {name}.",
                           "", "_If that's wrong, they can rebind and free it._"],
                    colour=TIER_DECLINE)
    bindings.bind(user_id, uid, display_name=display_name, nickname=name)
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
    if cfg.save_dir is None:
        log.info("no game.save_dir configured: 'nearest' will rank by cluster size")
        return None
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


class FeedbackView(discord.ui.View):
    """Labelling buttons under an answer card.

    **Components, not reactions.** A button row rides in the same `send()` payload at no
    extra API cost, where six reactions are six REST calls that would have to be deferred
    behind the answer like `art_post` is. The card is posted once, with its controls.

    Four buttons. Three are diagnoses that each route to a different fix - a mis-heard
    name is a lexicon problem, a wrong class is a routing problem, and a wrong entity on
    a clean transcript is neither. The fourth asks for a sentence instead of a diagnosis,
    and leads the row; see `capture.UNEXPECTED` for why it earned the slot.

    `timeout=None` keeps them live for the session. They do NOT survive a bot restart -
    persistent views need registering at startup, and this is a testbed control rather
    than a promise to the player. **That expiry is why `/palintel wrong` exists**: the
    two defects this session found were both "I acted on the card and the world
    disagreed", knowable only after travelling, by which time the buttons are long out of
    view. The message-id join was always retroactive; only the buttons were not.
    """

    def __init__(self, capture: SessionCapture):
        super().__init__(timeout=None)
        for kind, (emoji, label) in FEEDBACK_KINDS.items():
            self.add_item(_FeedbackButton(capture, kind, emoji, label))


class _NoteModal(discord.ui.Modal):
    """One optional field, asking what was expected instead.

    A modal rather than a follow-up message because it costs nothing until it is opened:
    the button rides in the card's own `send()` payload, and this is created only on a
    click. Nothing is required, so a mis-click is one Escape away and never a half-written
    row in the log.

    **py-cord, not discord.py**, and the two differ here in ways that fail at click time
    rather than at import: the submit hook is `callback`, not `on_submit`; the field is
    `InputText`; and the style enum is `InputTextStyle`, since `discord.TextStyle` does
    not exist in this library at all. Written with py-cord's own names so the mismatch is
    visible in the source rather than discovered by a player pressing a button.
    """

    def __init__(self, capture: SessionCapture, message_id: int):
        super().__init__(title="What did you expect?")
        self._capture, self._message_id = capture, message_id
        self._note = discord.ui.InputText(
            label="What went wrong?",
            placeholder="e.g. sent me to a level 70 area I can't survive",
            style=discord.InputTextStyle.paragraph,
            max_length=NOTE_LIMIT,
            required=False)
        self.add_item(self._note)

    async def callback(self, interaction: discord.Interaction) -> None:
        note = self._note.value or ""
        self._capture.record_feedback(
            self._message_id, UNEXPECTED,
            who=getattr(interaction.user, "display_name", None),
            note=note)
        log.info("feedback %s on message %s: %r", UNEXPECTED, self._message_id, note[:80])
        await interaction.response.send_message("Noted - thanks.", ephemeral=True)


class _FeedbackButton(discord.ui.Button):
    def __init__(self, capture: SessionCapture, kind: str, emoji: str, label: str):
        super().__init__(
            # The free-text one is the primary action and looks like it.
            style=(discord.ButtonStyle.primary if kind == UNEXPECTED
                   else discord.ButtonStyle.secondary),
            emoji=emoji, label=label)
        self._capture, self._kind = capture, kind

    async def callback(self, interaction: discord.Interaction) -> None:
        # Keyed by the MESSAGE, not by "the last utterance" - which breaks the moment two
        # more questions follow, and this is meant to be usable minutes later.
        if self._kind == UNEXPECTED:
            # A modal IS the response to the interaction, so nothing is written until the
            # player submits - and `send_message` must not be called before it.
            await interaction.response.send_modal(
                _NoteModal(self._capture, interaction.message.id))
            return
        self._capture.record_feedback(
            interaction.message.id, self._kind,
            who=getattr(interaction.user, "display_name", None))
        log.info("feedback %s on message %s", self._kind, interaction.message.id)
        # Ephemeral: the acknowledgement is for the person who clicked, and a public
        # "noted" under every corrected card is noise for everyone else.
        await interaction.response.send_message("Noted - thanks.", ephemeral=True)


async def _answer(channel, pipe: Pipeline, text: str, who: str,
                  activity: ActivityLog | None = None, watcher=None,
                  started: float | None = None, channel_kind: str = "text",
                  capture: SessionCapture | None = None, uid: str | None = None,
                  feedback: bool = False,
                  spend: "spend_mod.SpendLog | None" = None,
                  user_id: str | None = None,
                  bindings: "identity.Bindings | None" = None) -> None:
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
    """
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
        player_uid, why = identity.resolve(bindings, user_id, watcher.players)
        state = watcher.state_for(player_uid)
    if state.tech is None and watcher is not None:
        # Worth a line in the log: an unbound speaker in a co-op world gets noticeably
        # weaker answers, and "why did it stop knowing where I am" should be answerable
        # without a debugger.
        log.info("no player state for %s (%s)", who, why)

    loop = asyncio.get_running_loop()
    t_route = time.monotonic()
    try:
        outcome = await loop.run_in_executor(None, pipe.handle, text, state, who)
    except Exception:
        # Never leave a query unanswered: silence is indistinguishable from the bot
        # being down, and the player is mid-game and cannot investigate.
        log.exception("pipeline failed on %r", text)
        if activity is not None:
            activity.record("failed", text[:80])
        await channel.send(embed=discord.Embed(
            title="Something broke",
            description="That query hit an internal error. It's logged.",
            color=0xC62828))
        return

    route_ms = (time.monotonic() - t_route) * 1000
    declined = isinstance(outcome.call, Decline)
    if activity is not None:
        activity.timed("route", route_ms, text[:60])
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
            uid=uid, wav=f"{uid}.wav", seconds=0.0, heard=text,
            path="decline" if declined else answered_by,
            tool=None if declined else outcome.call.name,
            entity=(args.get("pal") or args.get("boss") or args.get("resource")
                    or args.get("item")),
            score=round(top.score, 3) if top else None,
            outcome="declined" if declined else "answered"))
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
    t_post = time.monotonic()
    try:
        # Text first, artwork second. The graded promise is "here are the coordinates",
        # and a picture is not part of it - so the answer goes out on its own round trip
        # and the upload happens after the clock has stopped. Sending them together would
        # charge every illustrated query the attachment's latency and move a bar that is
        # already unmet (Docs/00-overview.md §7).
        # The controls ride in this same call when enabled, so they cost nothing on the
        # graded path - unlike reactions, which would be one round trip each.
        message = await channel.send(
            embeds=[to_embed(c, i, with_art=False)
                    for i, c in enumerate(outcome.cards)],
            **({"view": FeedbackView(capture)} if (feedback and capture) else {}))
        if capture is not None and uid:
            # After the send, never before: the join key does not exist until Discord
            # has assigned it, and nothing upstream waits for it.
            capture.attach_message(uid, message.id)
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
            activity.timed(kind_timed, (time.monotonic() - started) * 1000, text[:60])

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
        files = art_files(outcome.cards)
    except Exception:
        log.exception("could not render card artwork for %r", text)
        return

    render_ms = (time.monotonic() - t_render) * 1000
    if not files:
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
        await message.edit(
            embeds=[to_embed(c, i) for i, c in enumerate(outcome.cards)],
            files=files)
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
    activity = ActivityLog()
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
    client = discord.Client(intents=intents)

    # Boxed, because a reconnect re-fires on_ready and a second polling task would double
    # the save reads for no benefit.
    tasks: dict[str, object] = {"save": None}

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
                asker=str(message.author.id))))
            return
        if low_iam := _IAM.match(text):
            await message.channel.send(embed=to_embed(_bind(
                bindings, watcher, str(message.author.id),
                message.author.display_name, low_iam.group(1).strip())))
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
        await _answer(message.channel, pipe, text, message.author.display_name,
                      activity, watcher, started=time.monotonic(),
                      channel_kind="text", spend=spend,
                      user_id=str(message.author.id), bindings=bindings)

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

    # One session id for the whole run, shared by capture and spend so a session's
    # clips, log and bill sit in one directory. Created here rather than inside the
    # voice block because TYPED queries cost money too, and the first version had the
    # spend log unreachable from the text handler for exactly that reason.
    session_id = time.strftime("%Y%m%d-%H%M%S")
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

        async def on_speech(utt, speaker=None) -> None:
            # `speaker` is None from the microphone, which cannot say who spoke, and a
            # Discord Member from the channel, which can. Everything else is shared.
            #
            # faster-whisper reads a file; the buffer is raw PCM. A scratch WAV is
            # cheaper and far less fragile than teaching the transcriber to take bytes,
            # and these are one-to-three second clips.
            uid = f"{int(time.time() * 1000):x}"
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
            activity.timed("stt", stt_ms, f"{utt.seconds:.1f}s audio")
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

            # **Observed when Discord can tell us, configured when only the mic can.**
            # The mic cannot say who spoke, so attribution is `voice.speaker` - naming the
            # person at the machine is what lets a spoken question be followed up in text,
            # which is what ADR-0012 promises. Unset, it stays "voice": guessing which
            # Discord user is sitting there would attribute speech to the wrong person in
            # a shared channel, and that is worse than not joining the two.
            #
            # A Discord packet carries its member, so on that source the guess disappears
            # and the promise holds for everyone in the channel rather than for one person
            # by declaration. `display_name` because that is the key the text path uses.
            who = (getattr(speaker, "display_name", None) or str(speaker)
                   if speaker is not None else cfg.voice.speaker or "voice")
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
    client.run(cfg.discord.token)


if __name__ == "__main__":
    run()
