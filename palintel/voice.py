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
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .listening import FRAME_SAMPLES, Utterance, UtteranceBuffer
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

    def feed(self, packet: bytes) -> list[Utterance]:
        """Push one Discord packet. Returns any utterances that closed."""
        samples = np.concatenate([self._tail, to_mono_16k(packet)])
        done: list[Utterance] = []

        n = len(samples) // FRAME_SAMPLES
        for i in range(n):
            frame = samples[i * FRAME_SAMPLES:(i + 1) * FRAME_SAMPLES]
            raw = frame.tobytes()

            score = self.wake.push(raw)
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


SPEECH_FLOOR = 300   # mean |int16| over an 80ms frame; below this is room tone


def make_sink(on_utterance: Callable[[int, Utterance], None],
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

        def stream_for(self, user_id: int) -> SpeakerStream:
            if user_id not in self._streams:
                kw = {"model": self._models} if self._models else {}
                self._streams[user_id] = SpeakerStream(
                    wake=WakeWord(threshold=self._threshold, **kw))
                log.info("voice: new speaker stream for %s", user_id)
            return self._streams[user_id]

        def write(self, data: bytes, user: int) -> None:
            """Called by py-cord for each decoded packet, on its decoder thread."""
            try:
                for utt in self.stream_for(user).feed(data):
                    self._on_utterance(user, utt)
            except Exception:
                # A raise here kills the receive loop for every speaker, and it presents
                # as the bot having silently stopped listening.
                log.exception("voice: dropping packet from %s after error", user)

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
