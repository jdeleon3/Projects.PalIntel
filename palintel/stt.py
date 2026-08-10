"""Speech-to-text — audio to text, with the lexicon supplied as decoding hints.

Promoted from tools/eval/score_stt.py, which established the settings measured in
Phase 0.6 ([ADR-0015](../Docs/adr/0015-local-gpu-stt.md)) and re-measured across 240
recordings in Phase 1. What survived the measurements:

  faster-whisper `medium.en`, float16, local GPU. `large-v3` was *less* accurate (80% vs
  88%) and slower - the `.en` models are English-specialised and bigger is not better.

  `hotwords`, not `initial_prompt`. `hotwords` is faster-whisper's keyterm-biasing
  mechanism; `initial_prompt` is a context hint and was measurably **hurting**, dropping
  the bare-name control group from 75% to 50% by steering the model toward a general
  context. This is the single most load-bearing line in the file.

Transcription is only the first layer. STT never once transcribed a variant Pal name
correctly across 25 recorded attempts (0%), and the pipeline still recovered 76% of them,
because `knowledge.Lexicon` repairs the mangled output afterwards. Do not read a raw
transcript as the pipeline's accuracy.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("palintel.stt")

MODEL = "medium.en"
# Whisper takes hotwords as one comma-joined string, and a very long list dilutes the
# bias. 400 was the eval's cap and is retained rather than re-derived - it is a knob
# nobody has measured, not a value anyone established.
MAX_HOTWORDS = 400


def hotword_order(lexicon) -> list[str]:
    """Decoding hints, resources first.

    Position in the list is not cosmetic - the bias decays along it, and the previous
    `sorted(canonical_names)` put the resources LAST by accident: they are the only
    lowercase entries, and ASCII sorts every capitalised Pal name ahead of them. So the
    four nouns Phase 1 can actually answer about sat at the bottom of 318 hints, behind
    313 Pal names that no registered tool can use.

    That was not theoretical. It is why a real session heard "goal" for coal and "a
    store" for ore, which then fell below the fast path's confidence floor and each cost
    a ~2s model round trip to recover. Measured over the 19 recorded resource clips, by
    whether the entity clears MIN_CONFIDENT afterwards - the exact condition the fast
    path tests:

        no hotwords         15/19   pal 38/60
        all, sorted         16/19   pal 44/60      <- what shipped
        resources only      19/19   pal 35/60      <- buys Q1 by wrecking Phase 2
        resources first     19/19   pal 42/60      <- this

    Resources-first is the only option that takes Q1 to 100% without abandoning the Pal
    names Phase 2 needs. It is not free: 2 of 60 Pal clips regressed, which on that
    sample is as likely noise as signal, and is worth re-measuring when Phase 2 registers
    a tool that depends on them.
    """
    resources = sorted(lexicon.resources())
    others = sorted(n for n in lexicon.canonical_names if n not in set(resources))
    return resources + others


class Transcriber:
    """faster-whisper with keyterm boosting, GPU when available.

    Constructed once and reused: model load is seconds and would otherwise land in the
    latency of the first query rather than in startup.
    """

    def __init__(self, lexicon, model: str = MODEL, device: str = "cuda"):
        try:
            from . import _cuda  # noqa: F401  registers NVIDIA DLL dirs on Windows
        except ImportError:
            pass
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "faster-whisper not installed:  pip install -r requirements.txt") from e

        self.hotwords = ", ".join(hotword_order(lexicon)[:MAX_HOTWORDS])

        # float16 on GPU, int8 on CPU: int8 exists to make CPU inference bearable and
        # gives up accuracy the GPU has no reason to sacrifice.
        ctype = "float16" if device == "cuda" else "int8"
        try:
            self.model = WhisperModel(model, device=device, compute_type=ctype)
            self.device = device
        except Exception as e:
            if device == "cpu":
                raise
            # CUDA failures surface at encode time, not construction - a missing cuBLAS
            # runtime once made this look like "local STT is not viable" when the GPU was
            # present and working (ADR-0015). Falling back is right; falling back
            # *silently* is what caused that, so it is a warning.
            log.warning("%s unavailable (%s: %s) - falling back to CPU, which measured "
                        "RTF 1.35 and will not hold the latency budget",
                        device, type(e).__name__, e)
            self.model = WhisperModel(model, device="cpu", compute_type="int8")
            self.device = "cpu"

    def transcribe(self, audio: str | Path, boost: bool = True) -> str:
        """Audio file to text. `boost=False` disables keyterm biasing, for A/B only."""
        kw = {"hotwords": self.hotwords} if boost else {}
        segs, _ = self.model.transcribe(str(audio), language="en", beam_size=5, **kw)
        return " ".join(s.text for s in segs).strip()


def available() -> bool:
    """True when faster-whisper is importable. Says nothing about the GPU."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
