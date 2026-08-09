"""Local microphone capture — the voice input path.

Replaces Discord voice receive, which is blocked upstream: Discord's DAVE end-to-end
encryption broke reception in py-cord, and `start_recording` now emits an unconditional
"currently broken" warning (Pycord-Development/pycord#3139). Attaching a sink there
succeeds and then delivers no audio, which is worse than failing.

A local mic turns out to suit this better than the shared channel ADR-0004 assumed:

  * The capture format is already what the detector wants - 16kHz mono int16, 1280-sample
    blocks - so there is no resampling and no packet-boundary remainder to carry. The
    Discord path needed both.
  * No network hop, so the wake word sees audio the moment it is spoken.
  * The player asking is the player at the second screen, which is the whole premise.

What it gives up is party members asking by voice; they keep the text path. That is a
real reduction against [ADR-0012](../Docs/adr/0012-dual-input-channels.md) and is
recorded as such rather than quietly dropped. `SpeakerStream` still keys by speaker, so
multi-speaker returns as configuration if py-cord's reception is ever fixed.

Discord remains the *output* surface throughout - cards in a channel on a second screen.
Only the input moved.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from .listening import FRAME_SAMPLES, SAMPLE_RATE, Utterance, UtteranceBuffer
from .wakeword import WakeWord

log = logging.getLogger("palintel.mic")

# Mean |int16| over an 80ms frame below which the frame is treated as a pause. Consulted
# only after the wake word has fired, so it separates pauses *within* an utterance from
# its end - not speech from silence on an idle channel.
SPEECH_FLOOR = 300


class MicListener:
    """Captures the default input device and emits utterances.

    `on_utterance` is called on the audio thread. It must not block: anything expensive
    - transcription, routing, posting to Discord - belongs on another thread, and
    stalling this one drops audio rather than merely delaying it.
    """

    def __init__(self, on_utterance: Callable[[Utterance], None],
                 models: list[str] | None = None, threshold: float = 0.5,
                 device: int | str | None = None, log=None):
        self._on_utterance = on_utterance
        self._device = device
        self._log = log
        self.device_name = "(not started)"
        kw = {"model": models} if models else {}
        self.wake = WakeWord(threshold=threshold, **kw)
        self.buffer = UtteranceBuffer()
        self._stream = None
        self._lock = threading.Lock()

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            # Overflow means frames were dropped before reaching us. Worth recording: it
            # presents as a wake word that intermittently fails to fire, which is
            # otherwise indistinguishable from a bad model.
            log.warning("mic: %s", status)
            if self._log is not None:
                self._log.record("overflow", str(status))
        try:
            with self._lock:
                pcm = bytes(indata)
                score = self.wake.push(pcm)
                if self.wake.fired(score):
                    log.info("mic: wake word %r at %.2f", self.wake.last_fired, score)
                    if self._log is not None:
                        self._log.record("wake", f"{self.wake.last_fired} at {score:.2f}")
                    self.buffer.trigger()
                import numpy as np
                amp = np.abs(np.frombuffer(pcm, dtype=np.int16)).mean()
                utt = self.buffer.push(pcm, is_speech=bool(amp > SPEECH_FLOOR))
            if utt is not None:
                self._on_utterance(utt)
        except Exception:
            # Raising here kills the stream and the bot goes silently deaf.
            log.exception("mic: dropping frame after error")

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "sounddevice not installed:  pip install -r requirements.txt") from e

        # blocksize is the detector's frame exactly, so no buffering or splitting is
        # needed between capture and detection.
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=FRAME_SAMPLES, device=self._device, callback=self._callback)
        self._stream.start()
        self.device_name = sd.query_devices(self._device or sd.default.device[0])["name"]
        log.info("mic: listening on %r for %s", self.device_name,
                 "+".join(self.wake.names))

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def available() -> bool:
    """True when an input device exists. False on a headless box with no mic."""
    try:
        import sounddevice as sd
        return any(d["max_input_channels"] > 0 for d in sd.query_devices())
    except Exception:
        return False


def devices() -> list[tuple[int, str]]:
    """Input devices, for `/palintel status` and for configuring `voice.device`."""
    import sounddevice as sd
    return [(i, d["name"]) for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0]
