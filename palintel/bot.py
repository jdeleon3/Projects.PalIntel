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
import logging
import sys
import time
import wave
from tempfile import TemporaryDirectory

from .activity import ActivityLog
from .cards import Card, recent_card, status_card
from .config import Config, ConfigError
from .knowledge import KnowledgeBase
from .pipeline import Pipeline, PlayerState, build_router
from .tools import Decline

log = logging.getLogger("palintel.bot")

try:
    import discord
except ImportError:  # pragma: no cover
    discord = None


def to_embed(card: Card) -> "discord.Embed":
    embed = discord.Embed(title=card.title,
                          description="\n".join(card.lines),
                          color=card.colour)
    if card.footer:
        embed.set_footer(text=card.footer)
    return embed


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
    return (f"{mic.device_name} - {'+'.join(mic.wake.names)} "
            f"@ {cfg.voice.threshold:g}")


def build_pipeline(cfg: Config) -> Pipeline:
    kb = KnowledgeBase.load(cfg.data_version)
    return Pipeline(kb, build_router(kb, router_config=cfg.router))


async def _answer(channel, pipe: Pipeline, text: str, who: str,
                  activity: ActivityLog | None = None, watcher=None,
                  started: float | None = None, channel_kind: str = "text") -> None:
    """Route `text` and post the cards. Shared by the text and voice paths.

    Routing is a network call and transcription is GPU work, so both run in the default
    executor: doing them inline would block the event loop and, with voice, stall the
    audio receive path behind an answer nobody is waiting on yet.

    `started` is when the player began waiting - end of speech for voice, message arrival
    for text - so the total covers everything they experience, including the Discord round
    trip. Passed in rather than taken here because the two paths start the clock in
    different places and only the caller knows where.
    """
    # Read at answer time, not cached at startup: the player moves, and "nearest" is
    # only worth answering against where they are now.
    state = PlayerState(player_coords=watcher.player_coords() if watcher else None)

    loop = asyncio.get_running_loop()
    t_route = time.monotonic()
    try:
        outcome = await loop.run_in_executor(None, pipe.handle, text, state)
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
    log.info("%s -> %s in %.0fms (%d card%s)", who, kind, route_ms, len(outcome.cards),
             "" if len(outcome.cards) == 1 else "s")
    # One message, several embeds. A query that resolves to a base Pal and its variant
    # has two correct answers; separate messages would let channel traffic interleave
    # and break the pairing that makes them readable.
    t_post = time.monotonic()
    try:
        await channel.send(embeds=[to_embed(c) for c in outcome.cards])
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
            activity.timed(channel_kind, (time.monotonic() - started) * 1000, text[:60])


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
                save=watcher.describe() if watcher else "not configured")))
            return
        if text.lower() in ("/palintel recent", "palintel recent"):
            await message.channel.send(embed=to_embed(recent_card(activity)))
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

        loop = asyncio.get_running_loop()
        tmp = TemporaryDirectory(prefix="palintel-voice-")

        async def on_speech(utt) -> None:
            # faster-whisper reads a file; the buffer is raw PCM. A scratch WAV is
            # cheaper and far less fragile than teaching the transcriber to take bytes,
            # and these are one-to-three second clips.
            path = f"{tmp.name}/{id(utt)}.wav"
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
            activity.record("heard", text[:80])
            await _answer(text_channel, pipe, text, "voice", activity, watcher,
                          started=utt.ended_at, channel_kind="voice")

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
