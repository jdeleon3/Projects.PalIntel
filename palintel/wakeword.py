"""Wake-word detection over a live audio stream (openWakeWord).

The acoustic gate that [ADR-0004](../Docs/adr/0004-wake-word-activation.md) specifies,
and that `activation.py` measured itself unable to replace. Text-level matching cannot
separate a genuine "hey pal" from party chatter - "hey paul" and "hey pal" share a
phonetic skeleton exactly - because the transcript has already discarded the acoustic
detail. This layer sees the waveform.

**The model is trained, not configured.** None of openWakeWord's six pretrained models
fire on "hey pal": measured across 60 real recordings, hey_jarvis peaked at 0.06 and
hey_mycroft at 0.000 against a 0.5 threshold. The phrase shape is wrong - they are all
"hey + two syllables" or a three-syllable name. `tools/wakeword/hey_pal.yaml` trained the
one this loads from `data/wakeword/`; `MODEL` can still name a pretrained model, which is
how the two were compared.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("palintel.wakeword")

MODEL = "hey_pal"
# The model trained on Discord-path audio. A SECOND model rather than a replacement: the
# codec costs the mic-trained model about 9% of its recall (21 of 236 clips fall below the
# firing threshold after a 64 kbps round trip - see tools/wakeword/discord_path.py), and
# the mic path is not improved by a model trained through a codec it never goes through.
# Which one loads follows `voice.source`; see `config.default_models`.
DISCORD_MODEL = "hey_pal_discord"
# openWakeWord's own names, which it resolves without a file on disk. Listed so a bare
# name that is NEITHER ours nor one of these can be reported as missing rather than
# handed to the library to fail on later.
PRETRAINED = frozenset({
    "alexa", "hey_mycroft", "hey_jarvis", "hey_rhasspy",
    "timer", "weather",
})
# Where trained models land. `hey_pal` is not one of openWakeWord's pretrained names, so
# a bare name has to be resolved here or the library raises "could not find pretrained
# model" at startup.
MODELS_DIR = Path(__file__).resolve().parents[1] / "data" / "wakeword"
# Inference is CPU-only: onnxruntime here has no CUDA provider, and 4s of audio costs
# ~51ms, a realtime factor near 0.01. It therefore never contends with the GPU, which
# matters because the GPU is simultaneously running STT and, during development, the
# wake-word training itself.
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

    def __init__(self, model: str | list[str] = MODEL, threshold: float = THRESHOLD,
                 models_dir: Path | None = None):
        """`model` may be one name or several.

        Several is the useful case during a transition: a pretrained model keeps the
        voice path working while a custom one is trained or evaluated, and both can run
        at once because inference is CPU-bound at ~1ms per 80ms frame. Whichever scores
        highest wins, so adding a model can only make the gate more sensitive - it never
        suppresses a detection the previous set would have made.
        """
        try:
            from openwakeword.model import Model
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "openwakeword not installed:  pip install -r requirements.txt") from e

        names = [model] if isinstance(model, str) else list(model)
        if not names:
            raise ValueError("at least one wake-word model is required")

        # A trained model is a path; a pretrained one is a bare name openWakeWord
        # resolves itself. Supporting both is what lets the voice path be built and
        # tested before "hey pal" finishes training, and what lets the two overlap
        # afterwards rather than requiring a cutover. A bare name that matches a file in
        # the models directory is ours; anything else is left for openWakeWord, so
        # `hey_jarvis` still resolves the way it always did.
        root = models_dir or MODELS_DIR
        specs, resolved = [], []
        for n in names:
            local = root / f"{n}.onnx"
            if n.endswith(".onnx") or local.exists():
                specs.append(str(local) if not n.endswith(".onnx") else n)
                resolved.append(n)
                continue
            if n in PRETRAINED:
                specs.append(n)                 # openWakeWord resolves it itself
                resolved.append(n)
                continue
            # A trained model that is not on disk. **Loud, and survivable.** This is
            # reachable the moment `voice.source` picks a model that has not been copied
            # into data/wakeword/ yet, and openWakeWord's own error for it is "could not
            # find pretrained model", which sends the reader looking in the wrong place.
            # Falling back keeps the voice path alive; failing here would make the bot
            # deaf, which ADR-0004 names as the worst failure because it presents as
            # nothing happening.
            log.error("wake-word model %r not found in %s - falling back to %r. "
                      "Train it, or copy the .onnx (and any .onnx.data) there.",
                      n, root, MODEL)
            if MODEL not in resolved and (root / f"{MODEL}.onnx").exists():
                specs.append(str(root / f"{MODEL}.onnx"))
                resolved.append(MODEL)
        if not specs:
            raise RuntimeError(
                f"no usable wake-word model. Looked for {names} in {root}")
        names = resolved
        self.model = Model(wakeword_models=specs, inference_framework="onnx")
        self.threshold = threshold
        self.names = names
        self.name = "+".join(names)
        self.last_fired: str | None = None
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
        if not scores:
            return 0.0
        winner = max(scores, key=scores.get)
        best = scores[winner]

        if self._refractory > 0:
            self._refractory -= 1
            return best
        if best >= self.threshold:
            self._refractory = REFRACTORY_FRAMES
            # Which model fired matters while several are loaded: it is how a custom
            # model's real-world recall gets attributed rather than inferred.
            self.last_fired = winner
            log.info("wake word %r fired at %.2f", winner, best)
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
