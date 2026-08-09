"""Wake-word detection over a live audio stream (openWakeWord).

The acoustic gate that [ADR-0004](../Docs/adr/0004-wake-word-activation.md) specifies,
and that `activation.py` measured itself unable to replace. Text-level matching cannot
separate a genuine "hey pal" from party chatter - "hey paul" and "hey pal" share a
phonetic skeleton exactly - because the transcript has already discarded the acoustic
detail. This layer sees the waveform.

**The model is trained, not configured.** None of openWakeWord's six pretrained models
fire on "hey pal": measured across 60 real recordings, hey_jarvis peaked at 0.06 and
hey_mycroft at 0.000 against a 0.5 threshold. The phrase shape is wrong - they are all
"hey + two syllables" or a three-syllable name. `tools/wakeword/hey_pal.yaml` trains one;
until it exists, `MODEL` can name any pretrained model so the rest of the voice path is
testable end to end.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("palintel.wakeword")

MODEL = "hey_pal"
# openWakeWord scores 0-1 per 80ms frame. 0.5 is upstream's default; the honest value
# comes from measuring recall against the 240 recorded utterances, which is why the
# constant is here rather than inlined.
THRESHOLD = 0.5
# Frames to ignore after firing. Without it a single "hey pal" scores above threshold on
# several consecutive frames and triggers the buffer repeatedly, which reads downstream
# as the player having said it three times.
REFRACTORY_FRAMES = 25  # ~2s


class WakeWord:
    """Streaming wake-word detector. One instance per speaker."""

    def __init__(self, model: str = MODEL, threshold: float = THRESHOLD,
                 models_dir: Path | None = None):
        try:
            from openwakeword.model import Model
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "openwakeword not installed:  pip install -r requirements.txt") from e

        # A trained model is a path; a pretrained one is a bare name openWakeWord
        # resolves itself. Supporting both is what lets the voice path be built and
        # tested before "hey pal" finishes training.
        spec = str(models_dir / f"{model}.onnx") if models_dir else model
        self.model = Model(wakeword_models=[spec], inference_framework="onnx")
        self.threshold = threshold
        self.name = model
        self._refractory = 0

    def reset(self) -> None:
        """Clear detector state. Call between utterances, not between frames."""
        self.model.reset()
        self._refractory = 0

    def push(self, frame: bytes) -> float:
        """Feed one 80ms frame of 16-bit mono PCM. Returns the best score seen.

        Returns the score rather than a bool so a caller can log near-misses. A wake word
        that scored 0.48 is the difference between "the model is wrong" and "the model
        nearly fired", and ADR-0004 calls false negatives silent failures precisely
        because that information is normally unavailable.
        """
        pcm = np.frombuffer(frame, dtype=np.int16)
        scores = self.model.predict(pcm)
        best = max(scores.values()) if scores else 0.0

        if self._refractory > 0:
            self._refractory -= 1
            return best
        if best >= self.threshold:
            self._refractory = REFRACTORY_FRAMES
            log.info("wake word %r fired at %.2f", self.name, best)
        return best

    def fired(self, score: float) -> bool:
        """True when `score` crossed the threshold and was not suppressed."""
        return score >= self.threshold and self._refractory == REFRACTORY_FRAMES


def available() -> bool:
    try:
        import openwakeword  # noqa: F401
        return True
    except ImportError:
        return False
