"""Measure a wake-word model against real recorded audio.

The training pipeline optimises against synthetic speech and a precomputed negative set.
Neither says whether the model fires when *this speaker* says the phrase into *this
microphone*, and that is the only question that matters. This scores against the
recordings collected for A5.

**Which clips contain the wake word is read from `prompts.json`, not assumed.** An
earlier version of this script assumed every recording began with "hey pal" and reported
one pooled number. It does not: the `control` prompts are bare entity names, recorded to
isolate STT on proper nouns. Four of them scored near zero and were counted as recall
failures, understating the model - and had they *fired*, they would have been counted as
successes while actually being false positives. Reading the label from ground truth costs
nothing and removes a whole class of quiet error.

Recall is measured on the clips that do contain the phrase. False positives are reported
separately and are **not** a meaningful rate: the corpus holds only a handful of
non-wake-word clips, and none of the continuous background chatter a real false-positive
measurement needs. Read it as a smoke test, not a number.

Read the score distribution, not just the pass rate. A model scraping over 0.5 on real
audio while scoring 0.99 on synthetic has not transferred, it has been lucky - and the
split between hard failures and near misses is what says whether lowering the threshold
would help or just add false positives.

    python tools/wakeword/evaluate.py --model hey_pal
    python tools/wakeword/evaluate.py --model hey_jarvis --limit 60
"""
from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
CLIPS = REPO / "data" / "stt_eval" / "quiet"
PROMPTS = REPO / "data" / "stt_eval" / "prompts.json"
WAKE = "hey pal"
FRAME = 1280           # 80ms at 16kHz, openWakeWord's native frame


def peak_score(model, wav: Path) -> float:
    """Highest score any frame of `wav` reaches. Peak, not mean.

    A wake word occupies a fraction of a second inside a several-second clip, so a mean
    would be dominated by the query that follows the phrase.
    """
    with wave.open(str(wav)) as f:
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    model.reset()
    best = 0.0
    for i in range(0, len(pcm) - FRAME, FRAME):
        scores = model.predict(pcm[i:i + FRAME])
        if scores:
            best = max(best, max(scores.values()))
    return best


def load_clips(limit: int = 0) -> tuple[list[Path], list[Path]]:
    """Recorded clips split by whether the prompt actually opens with the wake word."""
    prompts = json.loads(PROMPTS.read_text(encoding="utf-8"))["prompts"]
    said = {p["id"]: p["text"].lower().startswith(WAKE) for p in prompts}

    positive, negative = [], []
    for wav in sorted(CLIPS.glob("P*.wav"), key=lambda p: int(p.stem[1:])):
        if wav.stem not in said:
            continue          # recorded but no longer in the prompt set
        (positive if said[wav.stem] else negative).append(wav)
    if limit:
        positive = positive[:limit]
    return positive, negative


def report(peaks: np.ndarray, threshold: float) -> None:
    fired = peaks >= threshold
    hard = peaks < 0.05
    near = (~fired) & (~hard)
    print(f"  recall      {fired.sum()}/{len(peaks)} = {fired.mean() * 100:.1f}%")
    print(f"  peak score  median {np.median(peaks):.3f}   mean {peaks.mean():.3f}   "
          f"min {peaks.min():.3f}   max {peaks.max():.3f}")
    # The split decides whether a lower threshold is worth anything. Misses bunched near
    # zero are the model not recognising the utterance at all, and no threshold recovers
    # those - it only buys false positives.
    print(f"  misses      {hard.sum()} scored under 0.05 (unrecoverable), "
          f"{near.sum()} between 0.05 and {threshold}")
    print("\n  recall at other thresholds:")
    for t in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        print(f"    {t:.1f}  {(peaks >= t).mean() * 100:5.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="hey_pal",
                    help="a pretrained name, or a path to a trained .onnx")
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    from openwakeword.model import Model

    spec = args.model
    if not spec.endswith(".onnx"):
        local = REPO / "data" / "wakeword" / f"{spec}.onnx"
        if local.exists():
            spec = str(local)

    model = Model(wakeword_models=[spec], inference_framework="onnx")
    positive, negative = load_clips(args.limit)
    if not positive:
        raise SystemExit(f"no recordings under {CLIPS}")

    print(f"model={args.model}  threshold={args.threshold}")
    print(f"clips: {len(positive)} with the wake word, {len(negative)} without\n")

    peaks = np.array([peak_score(model, w) for w in positive])
    report(peaks, args.threshold)

    if negative:
        neg = np.array([peak_score(model, w) for w in negative])
        print(f"\n  non-wake-word clips: {(neg >= args.threshold).sum()}/{len(neg)} fired "
              f"(max {neg.max():.3f})")
        print("  -- too few, and no continuous chatter, to call this a false-positive "
              "rate")

    worst = np.argsort(peaks)[:5]
    print("\n  weakest clips (these are the silent failures ADR-0004 warns about):")
    for i in worst:
        print(f"    {peaks[i]:.3f}  {positive[i].name}")


if __name__ == "__main__":
    main()
