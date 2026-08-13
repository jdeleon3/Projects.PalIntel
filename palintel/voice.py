"""Discord voice receive — per-speaker streams into the wake-word gate.

The transport half of [ADR-0004](../Docs/adr/0004-wake-word-activation.md). Everything
with logic in it lives elsewhere and is tested offline: `wakeword.py` decides when the
phrase fired, `listening.py` decides where the utterance ends. This module only converts
Discord's audio into the format those expect and keeps one pipeline per speaker.

**Per speaker, not per channel.** Two people talking at once is two streams; mixing them
produces audio that transcribes as neither, and the wake word would be evaluated against
a blend nobody said. `Sink.write` already arrives tagged with a user, so the split is
free - the work is remembering to keep it.

**Format.** Discord delivers 48kHz 16-bit stereo PCM per packet; openWakeWord and
faster-whisper both want 16kHz mono. Downmix then decimate by 3, and hold the remainder
between packets - Discord's 20ms packets are 960 frames, which is not a whole number of
80ms detector frames, so dropping the remainder would discard audio at every packet
boundary and quietly corrupt the phrase.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .listening import FRAME_SAMPLES, SILENCE_MS, Utterance, UtteranceBuffer
from .wakeword import WakeWord

log = logging.getLogger("palintel.voice")

DISCORD_RATE = 48_000
TARGET_RATE = 16_000
DECIMATION = DISCORD_RATE // TARGET_RATE   # 3


def to_mono_16k(packet: bytes) -> np.ndarray:
    """48kHz stereo int16 bytes -> 16kHz mono int16 samples.

    Straight decimation rather than a resampling filter. The band above 8kHz that this
    aliases carries almost nothing for speech, both downstream models are trained on
    16kHz telephony-ish audio, and a proper polyphase filter per packet costs latency in
    the hot path for no measured gain. Revisit only if wake-word recall says otherwise.
    """
    a = np.frombuffer(packet, dtype=np.int16)
    if a.size % 2:                      # odd sample count: drop the stray half-frame
        a = a[:-1]
    mono = a.reshape(-1, 2).mean(axis=1).astype(np.int16)
    return mono[::DECIMATION]


@dataclass
class SpeakerStream:
    """One speaker's detector, buffer, and leftover samples."""
    wake: WakeWord
    buffer: UtteranceBuffer = field(default_factory=UtteranceBuffer)
    _tail: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int16))
    # Highest wake-word score since the last read, and the loudest frame behind it.
    # ADR-0004 calls a false negative a silent failure; without these two numbers a
    # detector scoring 0.48 on every attempt and one receiving silence look identical
    # from the log, which is the position this project spent a session in.
    peak_score: float = 0.0
    peak_level: float = 0.0
    _last_feed_at: float = 0.0
    _idle: bool = True

    def feed(self, packet: bytes) -> list[Utterance]:
        """Push one Discord packet. Returns any utterances that closed."""
        self._last_feed_at = time.monotonic()
        self._idle = False
        samples = np.concatenate([self._tail, to_mono_16k(packet)])
        done: list[Utterance] = []

        n = len(samples) // FRAME_SAMPLES
        for i in range(n):
            frame = samples[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]
            raw = frame.tobytes()

            score = self.wake.push(raw)
            self.peak_score = max(self.peak_score, score)
            self.peak_level = max(self.peak_level, float(np.abs(frame).mean()))
            if self.wake.fired(score):
                self.buffer.trigger()

            # Amplitude gate rather than a VAD model. The buffer only consults this once
            # the wake word has already fired, so it is separating speech from pauses
            # inside one utterance - not speech from silence across an idle channel,
            # which is the job a real VAD would be needed for.
            speech = bool(np.abs(frame).mean() > SPEECH_FLOOR)
            closed = self.buffer.push(raw, is_speech=speech)
            if closed is not None:
                done.append(closed)

        # Keep the remainder: 20ms Discord packets do not divide into 80ms frames.
        self._tail = samples[n * FRAME_SAMPLES:]
        return done

    def tick(self) -> list[Utterance]:
        """Handle a gap in transmission: close the utterance, and clear stale context.

        Discord stops transmitting when a speaker stops talking, which has two separate
        consequences and both are handled here.

        **The utterance never closes.** `UtteranceBuffer` counts quiet frames, and after
        the last word no frames arrive at all, so it waits for audio that is not coming.

        **The detector carries audio across the gap.** `_tail` holds the remainder of the
        last packet - Discord's 20ms packets do not divide into 80ms frames - and
        openWakeWord keeps its own rolling context. When speech resumes, the first frame
        of the new phrase is a splice of minutes-old audio onto "hey pal", exactly where
        the model is most sensitive. A microphone never does this, because its audio is
        continuous; `WakeWord.reset` exists for precisely this boundary ("call between
        utterances, not between frames") and nothing was calling it.

        This is the remaining candidate for live recall scattering across 0.11-0.97 while
        the channel itself is measurably clean: `harness/opus_channel_ab.py` puts a full
        Opus round trip at 0.957 against a 0.951 microphone baseline, so encoding and
        resampling cost nothing, and the gap is the only thing left that a file cannot
        reproduce.
        """
        closed = self.buffer.tick()
        out = [closed] if closed is not None else []

        if (not self._idle and self._last_feed_at
                and (time.monotonic() - self._last_feed_at) * 1000 >= SILENCE_MS):
            # Same threshold the buffer closes on, deliberately: one definition of "this
            # speaker stopped", so the detector and the segmenter cannot disagree about
            # where an utterance ended.
            self._idle = True
            self._tail = np.zeros(0, dtype=np.int16)
            self.wake.reset()
        return out


SPEECH_FLOOR = 300   # mean |int16| over an 80ms frame; below this is room tone


def make_sink(on_utterance: Callable[[object, Utterance], None],
              models: list[str] | None = None, threshold: float = 0.5):
    """Build a py-cord Sink that runs the wake-word pipeline instead of writing files.

    py-cord's built-in sinks buffer a whole recording and hand it over when recording
    *stops*, which is the wrong shape here - a query has to be answered while the player
    is still waiting for it. `Sink.write` is called per packet, so overriding it turns
    the same API into a stream.

    A factory rather than a module-level class because `discord.sinks` cannot be
    imported without py-cord's voice extras, and importing this module must not require
    them: the text-only path shares it.

    Subclassing `Sink` is not optional decoration - `start_recording` does an isinstance
    check and rejects a duck-typed object outright.
    """
    from discord.sinks import Sink

    class WakeWordSink(Sink):
        def __init__(self) -> None:
            super().__init__()
            self._on_utterance = on_utterance
            self._models = models
            self._threshold = threshold
            self._streams: dict[int, SpeakerStream] = {}
            # Kept so `tick` can attribute a timed-out utterance to the same object
            # `write` would have used - the display name on it is what conversation
            # memory keys on (ADR-0013), and an id would lose that.
            self._sources: dict[int, object] = {}

        def stream_for(self, user_id: int) -> SpeakerStream:
            if user_id not in self._streams:
                kw = {"model": self._models} if self._models else {}
                self._streams[user_id] = SpeakerStream(
                    wake=WakeWord(threshold=self._threshold, **kw))
                log.info("voice: new speaker stream for %s", user_id)
            return self._streams[user_id]

        def write(self, data, source) -> None:
            """Called by py-cord for each decoded packet, on its decoder thread.

            **py-cord 2.8's signature, not 2.7's.** This took `(data: bytes, user: int)`
            until 2026-08-13, which is what 2.7 passed. 2.8's router calls
            `sink.write(data, data.source)`: `data` is a `VoiceData` carrying `.pcm`, and
            `source` is a `Member`/`User`/`Object` rather than an id. Against 2.8 the old
            signature is *called normally* and then feeds a `VoiceData` object to
            `np.frombuffer`, so the sink accumulates nothing and the bot is silently deaf -
            the exact failure this whole path was mothballed for.

            `source` is kept whole rather than reduced to an id here. It is what makes
            attribution observed instead of configured: the display name on it is the key
            conversation memory uses (ADR-0013), and the mic could never supply it.
            """
            try:
                pcm = getattr(data, "pcm", data)
                uid = getattr(source, "id", source)
                self._sources[uid] = source
                for utt in self.stream_for(uid).feed(pcm):
                    self._on_utterance(source, utt)
            except Exception:
                # A raise here kills the receive loop for every speaker, and it presents
                # as the bot having silently stopped listening.
                log.exception("voice: dropping packet from %s after error", source)

        def peaks(self) -> dict[int, tuple[float, float]]:
            """Per-speaker (best wake score, loudest frame) since the last call."""
            out = {}
            for user_id, stream in list(self._streams.items()):
                out[user_id] = (stream.peak_score, stream.peak_level)
                stream.peak_score = stream.peak_level = 0.0
            return out

        def tick(self) -> None:
            """Drive wall-clock utterance closure for every speaker.

            Called on the event loop rather than from `write`, because the whole point
            is the case where `write` has stopped being called.
            """
            for user_id, stream in list(self._streams.items()):
                for utt in stream.tick():
                    self._on_utterance(self._sources.get(user_id, user_id), utt)

        def cleanup(self) -> None:
            # Deliberately not super().cleanup(): the base class formats and closes the
            # per-user audio files it has been accumulating, and this sink never writes
            # any - it consumes packets and discards them.
            self.finished = True
            self._streams.clear()

    return WakeWordSink()


def available() -> bool:
    """True when py-cord's voice extras are installed.

    py-cord 2.8 moved voice onto `davey`; `pip install py-cord` alone imports fine and
    then fails at connect time, so this checks the thing that actually breaks.
    """
    try:
        from discord.voice import VoiceClient  # noqa: F401
        return True
    except Exception:
        return False
