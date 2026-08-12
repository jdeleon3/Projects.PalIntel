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


# The resources hoisted ahead of the Pal names. NOT every resource: hoisting is a budget,
# and each entry pushed to the front costs Pal accuracy behind it (see hotword_order).
# These five are the ones the recorded evaluation set actually exercises.
VOICE_RESOURCES = ("ore", "coal", "sulfur", "quartz", "crude_oil")


def hotword_order(lexicon) -> list[str]:
    """Decoding hints: the spoken resources, then the Pals, then the rest.

    Position is not cosmetic - the bias decays along the list, and `sorted(canonical_
    names)` put the resources LAST by accident, since they are the only lowercase entries
    and ASCII sorts every capitalised Pal name ahead of them. A real session heard "goal"
    for coal and "a store" for ore, each falling below the fast path's floor and costing
    a ~2s model round trip.

    Phase 1 fixed that by hoisting ALL resources and recorded the cost as 2 of 60 Pal
    clips - "as likely noise as signal, worth re-measuring when Phase 2 registers a tool
    that depends on them". Re-measured over 185 clips with `find_pal_spawns` live
    (tools/eval/score_hotwords.py), scoring by whether the expected entity clears the
    floor the fast path tests, 0.78 for a resource and 0.85 for a Pal:

        variant            resource      pal
        none                  15/19   83/166
        all, sorted           16/19  100/166   <- identical to pals-first, byte for byte
        all resources first   19/19   92/166   <- Phase 1's choice
        pals first            16/19  100/166
        core resources first  19/19  101/166   <- this
        + stone/wood/paldium  19/19   97/166

    **The 2-clip regression was signal, not noise: on 166 clips it is 8.** But the cause
    was the NUMBER of hoisted entries, not hoisting resources - the set grew from 5 to 19
    in Phase 2, and pushing fourteen more strings ahead of 313 Pal names is what displaced
    them. Hoisting only the five the eval set exercises is strictly better than every
    other ordering measured, on both classes at once.

    A miss here is not a wrong answer - the floors still hold - it is a fast-path miss
    that costs a model round trip.

    **Known gap.** Stone, wood and paldium have no recorded clips and are almost certainly
    common in real play, but hoisting them costs a measured 4 Pal clips for an unmeasured
    gain. They stay unhoisted until there are clips to settle it with.

    Using display names instead of the canonical ids ("Hexolite Quartz" rather than
    `hexolite_quartz`) changed 82 of 185 transcripts and moved neither column, so the ids
    stay.
    """
    resources = set(lexicon.resources())
    core = [r for r in VOICE_RESOURCES if r in resources]
    rest = sorted(resources - set(core))
    # The eight tower leaders go LAST, and the position is the whole decision. They are
    # proper nouns STT has every reason to mangle - "Bjorn", "Auri" - so leaving them out
    # would guarantee the counter branch never sees them. But the finding above is that
    # hoisting is a budget paid in displaced Pal names, and there is not one recorded
    # clip of any of these eight to settle where they belong. The tail is the position
    # that cannot cost anything already measured; it moves once there are clips.
    return core + sorted(lexicon.pals()) + rest + sorted(lexicon.leaders())


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
