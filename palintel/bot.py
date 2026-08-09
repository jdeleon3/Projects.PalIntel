"""Discord adapter — a thin layer over Pipeline.

Deliberately thin. All understanding and answering lives in Pipeline, so the bot adds
only transport. Text and voice converge on the same `Pipeline.handle`
([ADR-0012](../Docs/adr/0012-dual-input-channels.md)); voice adds wake-word detection and
transcription in front of it and nothing else.

**Two threads, and the boundary is the only hard part here.** py-cord decodes voice on
its own thread and calls `Sink.write` there, while everything that talks to Discord must
run on the asyncio loop. Transcription is a third problem again: ~200ms of GPU work that
must not block either. So the voice path is: decoder thread detects and buffers ->
`run_coroutine_threadsafe` hands the closed utterance to the loop -> the loop pushes STT
and routing into an executor -> the result is posted from the loop. Getting this wrong
does not crash; it stalls the audio receive loop and the bot goes quietly deaf.

    python -m palintel.bot
"""
from __future__ import annotations

import asyncio
import logging
import sys
import wave
from tempfile import TemporaryDirectory

from .cards import Card
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


def build_pipeline(cfg: Config) -> Pipeline:
    kb = KnowledgeBase.load(cfg.data_version)
    return Pipeline(kb, build_router(kb))


async def _answer(channel, pipe: Pipeline, text: str, who: str) -> None:
    """Route `text` and post the cards. Shared by the text and voice paths.

    Routing is a network call and transcription is GPU work, so both run in the default
    executor: doing them inline would block the event loop and, with voice, stall the
    audio receive path behind an answer nobody is waiting on yet.
    """
    loop = asyncio.get_running_loop()
    try:
        outcome = await loop.run_in_executor(None, pipe.handle, text, PlayerState())
    except Exception:
        # Never leave a query unanswered: silence is indistinguishable from the bot
        # being down, and the player is mid-game and cannot investigate.
        log.exception("pipeline failed on %r", text)
        await channel.send(embed=discord.Embed(
            title="Something broke",
            description="That query hit an internal error. It's logged.",
            color=0xC62828))
        return

    kind = "decline" if isinstance(outcome.call, Decline) else outcome.call.name
    log.info("%s -> %s (%d card%s)", who, kind, len(outcome.cards),
             "" if len(outcome.cards) == 1 else "s")
    # One message, several embeds. A query that resolves to a base Pal and its variant
    # has two correct answers; separate messages would let channel traffic interleave
    # and break the pairing that makes them readable.
    await channel.send(embeds=[to_embed(c) for c in outcome.cards])


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
    summary = pipe.kb.summary()
    log.info("config: %s", cfg.redacted())
    log.info("loaded: %s", summary)

    # message_content is a PRIVILEGED intent. Without it every message arrives with an
    # empty body and the bot looks broken while connecting fine - see
    # Docs/discord-setup.md step 3.
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        channel = client.get_channel(cfg.discord.channel_id)
        where = f"#{channel.name}" if channel else f"id {cfg.discord.channel_id} (NOT FOUND)"
        log.info("connected as %s, listening in %s", client.user, where)
        if channel is None:
            log.error("channel not visible: check the id, and that the bot was invited "
                      "to that server with permission to view the channel")

    @client.event
    async def on_message(message: "discord.Message") -> None:
        # Loop prevention. A bot replying to itself is the classic Discord failure and
        # it escalates fast, so this guard comes before anything else.
        if message.author == client.user or message.author.bot:
            return
        if message.channel.id != cfg.discord.channel_id:
            return

        text = message.content.strip()
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
        await _answer(message.channel, pipe, text, message.author.display_name)

    async def start_voice() -> None:
        """Join the voice channel and stream every speaker through the wake word."""
        from .stt import Transcriber
        from .voice import make_sink

        channel = client.get_channel(cfg.voice.channel_id)
        if channel is None:
            log.error("voice channel %s not visible - check the id and that it is a "
                      "VOICE channel", cfg.voice.channel_id)
            return
        text_channel = client.get_channel(cfg.discord.channel_id)

        # Loaded once. A per-utterance load would put seconds of model initialisation
        # into the latency of every query.
        transcriber = Transcriber(pipe.kb.lexicon)
        log.info("voice: STT on %s", transcriber.device)

        loop = asyncio.get_running_loop()
        tmp = TemporaryDirectory(prefix="palintel-voice-")

        async def on_speech(user_id: int, utt) -> None:
            member = channel.guild.get_member(user_id)
            who = member.display_name if member else str(user_id)
            # faster-whisper reads a file; the buffer is raw PCM. Writing a scratch WAV
            # is cheaper and far less fragile than teaching the transcriber to take
            # bytes, and these are ~1-3 second clips.
            path = f"{tmp.name}/{user_id}-{id(utt)}.wav"
            with wave.open(path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16_000)
                w.writeframes(utt.pcm)

            text = await loop.run_in_executor(None, transcriber.transcribe, path)
            log.info("voice: %s said %r (%.1fs, closed on %s)",
                     who, text, utt.seconds, utt.reason)
            if not text.strip():
                # Wake word fired on something that was not speech. Silence is right:
                # posting "I didn't catch that" to a false trigger is channel noise for
                # a query nobody made.
                return
            await _answer(text_channel, pipe, text, f"voice/{who}")

        def dispatch(user_id: int, utt) -> None:
            # Called on py-cord's decoder thread. Nothing here may block or touch the
            # loop directly, so the whole answer is handed over and forgotten.
            asyncio.run_coroutine_threadsafe(on_speech(user_id, utt), loop)

        sink = make_sink(dispatch, models=list(cfg.voice.models),
                         threshold=cfg.voice.threshold)
        vc = await channel.connect()
        # The finished-callback is required by the API and fires only when recording
        # stops, which for this bot means shutdown. Nothing to do there.
        vc.start_recording(sink, lambda *_: None)
        log.info("voice: listening in #%s for %s", channel.name,
                 " / ".join(cfg.voice.models))

    @client.event
    async def on_connect() -> None:
        # Registered separately from on_ready: on_ready can fire again on reconnect, and
        # joining voice twice raises rather than being idempotent.
        pass

    if cfg.voice.enabled:
        _orig_ready = on_ready

        @client.event
        async def on_ready() -> None:  # noqa: F811  (replaces the text-only handler)
            await _orig_ready()
            if client.voice_clients:
                return          # already connected; a reconnect re-fires on_ready
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
