"""Local microphone capture — the voice input path.

Replaces Discord voice receive, which is blocked upstream: Discord's DAVE end-to-end
encryption broke reception in py-cord, and `start_recording` now emits an unconditional
"currently broken" warning (Pycord-Development/pycord#3139). Attaching a sink there
succeeds and then delivers no audio, which is worse than failing.

A local mic turns out to suit this better than the shared channel ADR-0004 assumed:

  * The capture format is close to what the detector wants - mono int16 in fixed blocks -
    so there is no packet-boundary remainder to carry, as the Discord path had. Rate is
    the one thing still negotiated: see `_capture_rate`.
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
from math import gcd
from typing import Callable

import numpy as np

from .listening import FRAME_SAMPLES, SAMPLE_RATE, Utterance, UtteranceBuffer
from .wakeword import WakeWord

log = logging.getLogger("palintel.mic")

# Mean |int16| over an 80ms frame below which the frame is treated as a pause. Consulted
# only after the wake word has fired, so it separates pauses *within* an utterance from
# its end - not speech from silence on an idle channel.
SPEECH_FLOOR = 300


class _Resampler:
    """Converts one capture block to exactly one 16kHz detector frame.

    Continuous across blocks. `resample_poly` filters each call in isolation, so
    resampling a block on its own leaves a transient at both edges - twelve times a
    second, at a fixed period. Feeding the previous block's tail through with it and
    discarding that part of the output is what makes the stream sound like one signal
    rather than a sequence of 80ms fragments.
    """

    def __init__(self, rate: int):
        from scipy.signal import resample_poly  # a hard dependency of openwakeword

        self._resample = resample_poly
        g = gcd(rate, SAMPLE_RATE)
        self.up, self.down = SAMPLE_RATE // g, rate // g
        if FRAME_SAMPLES * self.down % self.up:
            raise ValueError(f"cannot capture at {rate}Hz: it does not divide evenly "
                             f"into {SAMPLE_RATE}Hz frames")
        self.blocksize = FRAME_SAMPLES * self.down // self.up
        # resample_poly's filter half-length, expressed in input samples and rounded up
        # to a whole number of output samples so the block arithmetic stays exact.
        half = 10 * max(self.up, self.down)
        self._pad_in = self.down * (half // (self.up * self.down) + 1)
        self._pad_out = self._pad_in * self.up // self.down
        self._tail = np.zeros(self._pad_in, dtype=np.int16)

    def push(self, pcm: bytes) -> bytes:
        block = np.frombuffer(pcm, dtype=np.int16)
        joined = np.concatenate((self._tail, block))
        self._tail = joined[-self._pad_in:]
        out = self._resample(joined.astype(np.float32), self.up, self.down)
        out = out[self._pad_out:self._pad_out + FRAME_SAMPLES]
        return np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes()


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
        self.rate = SAMPLE_RATE
        self._resampler: _Resampler | None = None
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
                if self._resampler is not None:
                    pcm = self._resampler.push(pcm)
                score = self.wake.push(pcm)
                if self.wake.fired(score):
                    log.info("mic: wake word %r at %.2f", self.wake.last_fired, score)
                    if self._log is not None:
                        self._log.record("wake", f"{self.wake.last_fired} at {score:.2f}")
                    self.buffer.trigger()
                amp = np.abs(np.frombuffer(pcm, dtype=np.int16)).mean()
                utt = self.buffer.push(pcm, is_speech=bool(amp > SPEECH_FLOOR))
            if utt is not None:
                self._on_utterance(utt)
        except Exception:
            # Raising here kills the stream and the bot goes silently deaf.
            log.exception("mic: dropping frame after error")

    def _info(self, sd) -> dict:
        """The chosen device's descriptor. `device=None` means the system default, which
        `query_devices` reports only when asked for that index by number."""
        dev = self._device if self._device is not None else sd.default.device[0]
        return sd.query_devices(dev)

    def _capture_rate(self, sd) -> int:
        """The rate to open at: 16kHz where the device allows it, else its own.

        Not every device does 16kHz. A WASAPI device runs in shared mode at whatever rate
        Windows mixes it at - 48kHz - and refuses to open at any other, so asking for
        16kHz is a PortAudioError at startup rather than a resample. Which devices are
        affected is not visible from the name: the same physical microphone appears once
        per host API, and its MME entry converts happily while its WASAPI entry does not.
        """
        try:
            sd.check_input_settings(device=self._device, channels=1, dtype="int16",
                                    samplerate=SAMPLE_RATE)
            return SAMPLE_RATE
        except Exception:
            return int(self._info(sd)["default_samplerate"])

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "sounddevice not installed:  pip install -r requirements.txt") from e

        self.rate = self._capture_rate(sd)
        self._resampler = _Resampler(self.rate) if self.rate != SAMPLE_RATE else None
        # blocksize is one detector frame's worth of capture, so each callback produces
        # exactly one frame and no buffering or splitting is needed in between.
        blocksize = self._resampler.blocksize if self._resampler else FRAME_SAMPLES
        self._stream = sd.RawInputStream(
            samplerate=self.rate, channels=1, dtype="int16",
            blocksize=blocksize, device=self._device, callback=self._callback)
        self._stream.start()
        self.device_name = self._info(sd)["name"]
        log.info("mic: listening on %r at %dHz%s for %s", self.device_name, self.rate,
                 f" (resampled to {SAMPLE_RATE})" if self._resampler else "",
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
