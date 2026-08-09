"""Measure a wake-word model against real recorded audio.

The training pipeline optimises against synthetic speech and a precomputed negative set.
Neither says whether the model fires when *this speaker* says the phrase into *this
microphone*, and that is the only question that matters. This scores against the 240
recordings collected for A5 - the same set that showed openWakeWord's pretrained models
firing 0 times out of 60 on "hey pal".

Every recording begins with the wake word, so recall is the fraction that fire. There is
no negative set here: false positives need continuous non-query audio, which the STT
corpus does not contain. Treat a high recall as necessary and not sufficient, and read
the firing-score distribution as well as the pass rate - a model scraping over 0.5 on
real audio while scoring 0.99 on synthetic has not transferred, it has been lucky.

    python tools/wakeword/evaluate.py --model hey_pal
    python tools/wakeword/evaluate.py --model hey_jarvis --limit 60
"""
from __future__ import annotations

import argparse
import glob
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
CLIPS = REPO / "data" / "stt_eval" / "quiet"
FRAME = 1280           # 80ms at 16kHz, openWakeWord's native frame


def peak_scores(model, wav: str) -> float:
    """Highest score any frame of `wav` reaches. Peak, not mean.

    A wake word occupies a fraction of a second inside a several-second clip, so a mean
    would be dominated by the query that follows the phrase.
    """
    with wave.open(wav) as f:
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    model.reset()
    best = 0.0
    for i in range(0, len(pcm) - FRAME, FRAME):
        scores = model.predict(pcm[i:i + FRAME])
        if scores:
            best = max(best, max(scores.values()))
    return best


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
    wavs = sorted(glob.glob(str(CLIPS / "P*.wav")))
    if args.limit:
        wavs = wavs[:args.limit]
    if not wavs:
        raise SystemExit(f"no recordings under {CLIPS}")

    print(f"model={args.model}  clips={len(wavs)}  threshold={args.threshold}\n")
    peaks = np.array([peak_scores(model, w) for w in wavs])
    fired = peaks >= args.threshold

    print(f"  recall      {fired.sum()}/{len(wavs)} = {fired.mean() * 100:.1f}%")
    print(f"  peak score  median {np.median(peaks):.3f}   "
          f"mean {peaks.mean():.3f}   min {peaks.min():.3f}   max {peaks.max():.3f}")
    print()
    print("  recall at other thresholds:")
    for t in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        print(f"    {t:.1f}  {(peaks >= t).mean() * 100:5.1f}%")

    if fired.mean() < 1.0:
        worst = np.argsort(peaks)[:5]
        print("\n  weakest clips (these are the silent failures ADR-0004 warns about):")
        for i in worst:
            print(f"    {peaks[i]:.3f}  {Path(wavs[i]).name}")


if __name__ == "__main__":
    main()
