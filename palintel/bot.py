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
import sys
import time
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

from . import activation
from .activity import ActivityLog
from .capture import FEEDBACK_KINDS, SessionCapture, Utterance
from .artwork import Artwork
from .cards import TIER_DECLINE, Card, recent_card, status_card
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
    # Who the mic is attributed to belongs here rather than nowhere: it decides whose
    # conversation memory a spoken query joins, and unattributed voice silently not
    # sharing a thread with the same person's typed follow-ups is invisible otherwise.
    heard_as = cfg.voice.speaker or "voice (unattributed - set voice.speaker)"
    return (f"{mic.device_name} - {'+'.join(mic.wake.names)} "
            f"@ {cfg.voice.threshold:g}, heard as {heard_as}")


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

    Three buttons, and no more, because each distinction routes to a different fix: a
    mis-heard name is a lexicon problem, a wrong class is a routing problem, and a wrong
    entity on a clean transcript is neither. A fourth nobody presses is clutter on every
    card, and card density is already an open decision in STATUS.

    `timeout=None` keeps them live for the session. They do NOT survive a bot restart -
    persistent views need registering at startup, and this is a testbed control rather
    than a promise to the player.
    """

    def __init__(self, capture: SessionCapture):
        super().__init__(timeout=None)
        for kind, (emoji, label) in FEEDBACK_KINDS.items():
            self.add_item(_FeedbackButton(capture, kind, emoji, label))


class _FeedbackButton(discord.ui.Button):
    def __init__(self, capture: SessionCapture, kind: str, emoji: str, label: str):
        super().__init__(style=discord.ButtonStyle.secondary, emoji=emoji, label=label)
        self._capture, self._kind = capture, kind

    async def callback(self, interaction: discord.Interaction) -> None:
        # Keyed by the MESSAGE, not by "the last utterance" - which breaks the moment two
        # more questions follow, and this is meant to be usable minutes later.
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
                  feedback: bool = False) -> None:
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
    # only worth answering against where they are now.
    state = PlayerState(player_coords=watcher.player_coords() if watcher else None)

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
    if capture is not None and uid:
        # What the SYSTEM decided, written as label "auto". Never as truth: labels taken
        # from the router's own behaviour are self-confirming, so a consistent bug would
        # be ratified by the corpus it produces. Only a human or a successful rephrase
        # promotes a label past auto.
        top = outcome.candidates[0] if outcome.candidates else None
        args = {} if declined else outcome.call.args
        capture.record(Utterance(
            uid=uid, wav=f"{uid}.wav", seconds=0.0, heard=text,
            path=("decline" if declined
                  else ("fast" if "cue" in (outcome.call.rationale or "") else "model")),
            tool=None if declined else outcome.call.name,
            entity=(args.get("pal") or args.get("boss") or args.get("resource")
                    or args.get("item")),
            score=round(top.score, 3) if top else None,
            outcome="declined" if declined else "answered"))
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
                router=pipe.router.name,
                artwork=_artwork_status(cfg, pipe))))
            return
        if text.lower() in ("/palintel recent", "palintel recent"):
            await message.channel.send(embed=to_embed(recent_card(activity)))
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
        await _answer(message.channel, pipe, text, message.author.display_name,
                      activity, watcher, started=time.monotonic(), channel_kind="text")

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
            except Exception:
                # poll() swallows its own failures; this catches anything above it, so a
                # bad save can never end the polling task and silently freeze "nearest"
                # at whatever position it last saw.
                log.exception("save poll failed")

    listener = {"mic": None}   # boxed so on_ready can see it across re-fires

    async def start_voice() -> None:
        """Listen on the local microphone and answer into the text channel.

        Input is the mic, not Discord voice: Discord's DAVE encryption broke reception
        in py-cord (pycord#3139), where `start_recording` accepts a sink and then
        delivers no audio - a failure that looks exactly like a wake word never firing.
        """
        from .mic import MicListener
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

        # One session per bot run. Off by default; see CaptureConfig for why capture and
        # feedback are two flags rather than one.
        capture = SessionCapture() if cfg.capture.enabled else None
        if capture is not None:
            log.info("capture: session %s -> %s", capture.session, capture.dir)

        loop = asyncio.get_running_loop()
        tmp = TemporaryDirectory(prefix="palintel-voice-")

        async def on_speech(utt) -> None:
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

            # The mic cannot say who spoke, so attribution is configuration: naming the
            # person at the machine is what lets a spoken question be followed up in
            # text, which is what ADR-0012 promises. Unset, it stays "voice" - guessing
            # which Discord user is sitting there would attribute speech to the wrong
            # person in a shared channel, and that is worse than not joining the two.
            await _answer(text_channel, pipe, text, cfg.voice.speaker or "voice",
                          activity, watcher,
                          started=utt.ended_at, channel_kind="voice",
                          capture=capture, uid=uid,
                          feedback=cfg.capture.feedback)

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

        def dispatch(utt) -> None:
            # Called on sounddevice's audio thread. Nothing here may block or touch the
            # loop directly, so the whole answer is handed over - but not forgotten; a
            # stall here drops audio rather than merely delaying it.
            asyncio.run_coroutine_threadsafe(on_speech(utt), loop).add_done_callback(
                _report)

        mic = MicListener(dispatch, models=list(cfg.voice.models),
                          threshold=cfg.voice.threshold, device=cfg.voice.device,
                          log=activity)
        mic.start()
        listener["mic"] = mic

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
