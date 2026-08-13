"""Discord voice receive — the other audio source, and the one ADR-0012 asked for.

`mic.py`'s counterpart. Both produce `Utterance`s and nothing downstream can tell which
one it came from, which is the whole point: `voice.source` in the config swaps them and
everything after the wake word is unchanged.

## Why this exists again

Voice receive was recorded here as *"upstream-blocked on DAVE"* from the day Discord made
its end-to-end encryption mandatory, and `mic.py` was built to replace it. **That reading
was wrong.** Measured against a live channel, DAVE decryption succeeds on 99.8% of
packets; what was broken is py-cord 2.8's receive plumbing, which shipped a new
`voice/receive/` package against the old `sinks/core.py` so `start_recording()` raised
before a single packet was read. `pydiscorddave` fixes nine defects in that path and
leaves the cryptography alone.

Worth stating plainly because it is the fourth time in this project: **the blockage was
named from a warning message rather than from a measurement**, and it cost the party-voice
feature for months. py-cord's own runtime warning says voice receive is "currently
broken", which is true and says nothing about why.

## What this buys over the microphone

**Attribution is observed rather than declared.** Every packet arrives tagged with the
member who sent it, so a spoken question and that person's typed follow-up join the same
conversation thread by construction (ADR-0012, ADR-0013). `mic.py` could only ever name
one speaker, in configuration, by hand - and its docstring records multi-speaker as
something that "returns as configuration if py-cord's reception is ever fixed".
`SpeakerStream` already keys by speaker and this layer mixes nothing, so that held.

## What it costs

A network hop before the wake word sees the audio, and a dependency on a patch against
py-cord internals. The failure it degrades to is the one ADR-0004 names as worst -
connected, recording, and silent, indistinguishable from nobody speaking - which is why
`voice.source` exists and why `mic` remains the default. Nothing here should be the only
way in.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable

from .listening import Utterance

log = logging.getLogger("palintel.discord_voice")


def patch_available() -> bool:
    """True when `pydiscorddave` is importable.

    Its absence is a normal state, not an error: it is an editable install of a sibling
    repository, so a fresh checkout of PalIntel alone will not have it.
    """
    try:
        import pydiscorddave  # noqa: F401
        return True
    except ImportError:
        return False


class DiscordListener:
    """Receives from a Discord voice channel and emits utterances.

    Shaped to match `MicListener` - `start()`, `stop()`, `device_name`, `wake_names` - so
    `bot.py` branches once on the config and not again anywhere else. `start()` is a
    coroutine here and synchronous there, which is the one difference the caller sees, and
    it is inherent: connecting to a voice channel is an await.

    `on_utterance(speaker, utt)` is called on py-cord's decoder thread and must not block,
    for the same reason `MicListener`'s must not: stalling it drops audio rather than
    delaying it. `speaker` is a `Member`/`User`/`Object`, not an id - the caller needs the
    display name, and reducing it here would throw away the thing this source exists for.
    """

    def __init__(self, on_utterance: Callable[[object, Utterance], None],
                 channel_id: int, models: list[str] | None = None,
                 threshold: float = 0.5, log_=None):
        self._on_utterance = on_utterance
        self._channel_id = channel_id
        self._models = models
        self._threshold = threshold
        self._log = log_
        self._vc = None
        self._patch = None
        self._sink = None
        self._health = None
        self._ticker = None
        self.device_name = "(not started)"
        self.wake_names = list(models or ["hey_pal"])

    async def start(self, client) -> None:
        """Install the receive patch, connect, and start recording.

        Ordering is not cosmetic: `pycord.install()` replaces methods on classes py-cord
        binds at connect time, so it has to run before `channel.connect()`. Installing
        after would patch objects the live `VoiceClient` no longer consults, and present as
        the fix having done nothing.
        """
        from pydiscorddave import pycord

        from .voice import make_sink

        channel = client.get_channel(self._channel_id)
        if channel is None:
            raise RuntimeError(
                f"voice channel {self._channel_id} is not visible to the bot - check "
                f"voice.channel_id is a VOICE channel in a guild the bot has joined")

        # None means py-cord already carries the upstream fix (pycord#3159), which is the
        # designed exit rather than a problem. Logged either way, because "the patch did
        # not apply" and "the patch was not needed" look identical from a silent channel.
        self._patch = pycord.install()
        log.info("discord voice: receive patch %s",
                 "installed" if self._patch is not None
                 else "not needed (py-cord already fixed)")
        # `install()` returning a handle only means it ran, not that the patches took.
        # They bind to classes py-cord instantiates at connect time, so an import-order
        # accident can leave some live and some not - which presents as reception being
        # partially broken rather than as an error. `diagnose()` names each one.
        log.info("discord voice: %s", pycord.diagnose())

        self._vc = await channel.connect()
        self._sink = make_sink(self._deliver, models=self._models,
                               threshold=self._threshold)
        self._vc.start_recording(self._sink, self._on_stopped)

        self.device_name = f"#{channel.name}"
        log.info("discord voice: recording in %r for %s", self.device_name,
                 "+".join(self.wake_names))

        self._health = asyncio.create_task(self._report_health())
        self._ticker = asyncio.create_task(self._tick_utterances())

    # Comfortably under SILENCE_MS (700ms) so the close lands within one tick of the
    # threshold rather than a tick late.
    TICK_INTERVAL = 0.2

    async def _tick_utterances(self) -> None:
        """Close utterances that ran out of packets rather than out of speech.

        Discord sends nothing while a speaker is silent, so the frame-driven silence
        counter in `UtteranceBuffer` never advances after the last word - the utterance
        stays open until that person speaks again. A local microphone has no such gap,
        which is why this is needed here and not in `mic.py`.
        """
        while True:
            await asyncio.sleep(self.TICK_INTERVAL)
            try:
                if self._sink is not None:
                    self._sink.tick()
            except Exception:
                # Same reasoning as the sink's own guard: a raise here would stop every
                # future close, and present as the bot hearing the first half of a
                # session and nothing after.
                log.exception("discord voice: utterance tick raised")

    # How often to log receive health. Long enough not to clutter a play session, short
    # enough that a session which went wrong has several samples in it.
    HEALTH_INTERVAL = 60

    async def _report_health(self) -> None:
        """Log delivery rate periodically while recording.

        The measurement that matters is **audio seconds delivered per wall-clock
        second**, and nothing else in the log shows it. A run where reception was
        arriving at 8% of realtime looked, from the log alone, exactly like a run where
        the speaker was thinking: the wake word fired, then nothing until the utterance
        closed 35 seconds later. The counters distinguish those two instantly and the
        transcript never can.

        Below ~0.9 the pipeline is falling behind and every utterance closes late, since
        the buffer only sees the silence that ends one after it has drained everything
        before it.
        """
        previous, last_at = 0.0, time.monotonic()
        while True:
            await asyncio.sleep(self.HEALTH_INTERVAL)
            if self._patch is None:
                return
            s = self._patch.stats
            now = time.monotonic()
            delivered = s.pcm_bytes / (48_000 * 2 * 2)
            rate = (delivered - previous) / max(now - last_at, 1e-6)
            previous, last_at = delivered, now

            # Peak wake score is the one number that separates "the detector never got
            # usable audio" from "it got audio and scored below threshold". Reported
            # even when nothing fired - especially then.
            peaks = self._sink.peaks() if self._sink is not None else {}
            heard = ", ".join(f"{uid}: wake<={score:.2f} level<={level_:.0f}"
                              for uid, (score, level_) in peaks.items()) or "no speakers"
            log.info("discord voice: detector | %s", heard)

            level = logging.INFO if rate >= 0.9 or delivered == 0 else logging.WARNING
            log.log(level,
                    "discord voice: %.2fx realtime (%.0fs audio) | %s",
                    rate, delivered, s.summary())

    def _resolve(self, speaker):
        """Turn whatever py-cord handed us into something that knows its own name.

        **py-cord 2.8 passes a `Member`, a `User`, or a bare `Object`**, and an `Object`
        is a snowflake with no name at all. Four of the 2026-08-13 voice sessions
        attributed 20 queries to the literal string `<Object id=366300806208552972>` - a
        Python repr - which then keyed conversation memory, the spend ledger and (once M4
        landed) the capture corpus. Found by putting the spend split on a screen and
        looking at it.

        Resolved against the guild's member cache, which is where the name actually is.
        An id that will not resolve is handed back unchanged and named honestly upstream;
        inventing a name for it would attribute speech to somebody.
        """
        if getattr(speaker, "display_name", None) is not None:
            return speaker
        uid = getattr(speaker, "id", None)
        guild = getattr(getattr(self._vc, "channel", None), "guild", None)
        if uid is None or guild is None:
            return speaker
        found = guild.get_member(uid)
        if found is None:
            # Worth a line: a persistent miss here means the members intent or the cache
            # is the problem, and the symptom is otherwise just an ugly name in a ledger.
            log.info("discord voice: no cached member for %s - attribution stays by id",
                     uid)
            return speaker
        return found

    def _deliver(self, speaker, utt: Utterance) -> None:
        """One closed utterance, on py-cord's decoder thread."""
        try:
            self._on_utterance(self._resolve(speaker), utt)
        except Exception:
            # Same reasoning as the sink's own guard: a raise on this thread takes
            # reception down for every speaker, and it presents as the bot going deaf.
            log.exception("discord voice: delivering an utterance raised")

    def _on_stopped(self, sink, *args) -> None:
        """py-cord's recording-finished callback.

        Recording ending on its own is a fault, not a lifecycle event - nothing here ever
        calls `stop_recording` except `stop()`. Recorded rather than raised, because this
        runs on py-cord's own thread and the useful outcome is a line in the log that
        explains a channel that went quiet.
        """
        log.warning("discord voice: recording stopped (%s)", args or "no reason given")
        if self._log is not None:
            self._log.record("failed", "discord voice recording stopped")

    def stats(self) -> dict:
        """The patch's receive counters, or `{}`.

        Worth surfacing rather than leaving in the package: after these fixes a corrupt
        frame no longer raises, so a counter moving is the only remaining evidence that
        something is wrong. `opus_errors` climbing while `ok` also climbs is the signature
        of partial corruption - the failure mode that sounds fine.
        """
        if self._patch is None:
            return {}
        s = self._patch.stats
        return {"ok": s.outcomes.get("ok", 0),
                "failed": sum(v for k, v in s.outcomes.items() if k != "ok"),
                "opus_errors": s.opus_errors,
                "concealed": s.opus_concealed,
                "writes": s.writes,
                "audio_seconds": s.pcm_bytes / (48_000 * 2 * 2),
                "discards": s.buffer_discards,
                # Non-zero means the wake-word path cannot keep up with arriving audio
                # and frames are being shed to stay current. It is the counter that
                # separates "reception is broken" from "our sink is too slow", which
                # looked identical for an entire debugging session.
                "sink_dropped": s.sink_dropped}

    def stop(self) -> None:
        for task_name in ("_health", "_ticker"):
            task = getattr(self, task_name)
            if task is not None:
                task.cancel()
                setattr(self, task_name, None)
        if self._vc is not None:
            try:
                self._vc.stop_recording()
            except Exception:
                log.debug("discord voice: stop_recording raised", exc_info=True)
        # The patch is left installed. Reverting it here would un-patch classes a
        # reconnecting VoiceClient still needs, and `Installed.revert()` exists for tests
        # and for an interactive session, not for a shutdown path.
