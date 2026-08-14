"""Score wake-word models across audio paths, and gate them on false positives.

`evaluate.py` answers "does this model fire when the phrase is spoken into the
microphone". This answers the question a second model exists for: **what happens on the
Discord path**, and — the part that decides whether a model is shippable at all — what it
does on codec'd audio containing no wake word.

Four populations, and the last one is the gate:

  mic          the 240 A5 recordings. What the shipping model was validated on.
  mic->opus    the same clips through a 64 kbps round trip. Paired, so the difference
               is attributable to the codec and nothing else.
  discord      clips captured during real Discord sessions. **Selection-biased**: a clip
               exists only because the wake word fired, so recall over it is 100% by
               construction and only the score distribution carries information.
  noise->opus  ESC-50 environmental audio through the same codec. No wake word anywhere
               in it, so every frame that scores above threshold is a false positive.

**Read the last row first.** A model trained on codec'd positives can learn "codec
artifacts" as a shortcut - the 2,000 hours of ACAV100M negatives are precomputed features
and cannot be round-tripped, so they stay clean and the shortcut is available. A model
that has taken it scores brilliantly on codec'd positives and fires on anything arriving
over Discord. Recall alone cannot distinguish that model from a good one; this can.

    python tools/wakeword/compare_paths.py
    python tools/wakeword/compare_paths.py --models hey_pal hey_pal_discord
    python tools/wakeword/compare_paths.py --threshold 0.1 --limit 60
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from discord_path import through_discord  # noqa: E402

MIC = REPO / "data" / "stt_eval" / "quiet"
PROMPTS = REPO / "data" / "stt_eval" / "prompts.json"
SESSIONS = REPO / "data" / "sessions"
NOISE = Path("C:/Users/jd02_/tools/owwtrain/data/background_discord")
MODEL_DIR = REPO / "data" / "wakeword"
FRAME = 1280
WAKE = "hey pal"


def load_model(name: str):
    from openwakeword.model import Model

    path = MODEL_DIR / f"{name}.onnx"
    if not path.exists():
        alt = Path("C:/Users/jd02_/tools/owwtrain/out") / f"{name}.onnx"
        if alt.exists():
            path = alt
        else:
            return None
    return Model(wakeword_models=[str(path)], inference_framework="onnx")


def pcm_of(wav: Path) -> np.ndarray:
    with wave.open(str(wav)) as f:
        data = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        if f.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1).astype(np.int16)
        return data


def peak(model, pcm: np.ndarray) -> float:
    """Highest score any frame reaches. Peak, not mean: the phrase is a fraction of a
    second inside a clip that keeps going."""
    model.reset()
    best = 0.0
    for i in range(0, len(pcm) - FRAME, FRAME):
        s = model.predict(pcm[i:i + FRAME])
        if s:
            best = max(best, max(s.values()))
    return best


def frames_over(model, pcm: np.ndarray, threshold: float) -> tuple[int, int]:
    """(frames above threshold, frames scored). For the false-positive population, where
    a peak per clip understates a model that fires repeatedly through a long recording."""
    model.reset()
    over = seen = 0
    for i in range(0, len(pcm) - FRAME, FRAME):
        s = model.predict(pcm[i:i + FRAME])
        if s:
            seen += 1
            if max(s.values()) >= threshold:
                over += 1
    return over, seen


def mic_clips(limit: int = 0) -> list[Path]:
    prompts = json.loads(PROMPTS.read_text(encoding="utf-8"))["prompts"]
    said = {p["id"]: p["text"].lower().startswith(WAKE) for p in prompts}
    out = [w for w in sorted(MIC.glob("P*.wav")) if said.get(w.stem)]
    return out[:limit] if limit else out


def discord_clips(limit: int = 0) -> list[Path]:
    """Captured gameplay clips from Discord-source sessions.

    Selected by date, which is an inference rather than a reading: `Utterance` records
    who spoke but not which audio path produced the clip. Worth fixing; noted here so the
    number is not read as firmer than it is.
    """
    out = []
    for d in sorted(SESSIONS.iterdir()):
        if d.is_dir() and d.name.startswith("20260813"):
            out.extend(sorted(d.glob("*.wav")))
    return out[:limit] if limit else out


def report_recall(name: str, peaks: list[float], threshold: float) -> None:
    a = np.array(peaks)
    if not len(a):
        print(f"    {name:<22} (no clips)")
        return
    fired = a >= threshold
    print(f"    {name:<22} n={len(a):<4} fired {fired.sum():>3}/{len(a)} "
          f"({fired.mean()*100:5.1f}%)  median {np.median(a):.3f}  "
          f"p10 {np.percentile(a, 10):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["hey_pal", "hey_pal_discord"])
    ap.add_argument("--threshold", type=float, default=0.1,
                    help="the shipping firing threshold (voice.threshold)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--noise-limit", type=int, default=300,
                    help="ESC-50 clips for the false-positive gate")
    args = ap.parse_args()

    mic = mic_clips(args.limit)
    disc = discord_clips(args.limit)
    noise = sorted(NOISE.glob("*.wav"))[:args.noise_limit] if NOISE.is_dir() else []
    print(f"mic {len(mic)} · discord-captured {len(disc)} · codec'd noise {len(noise)}")
    if not noise:
        print(f"\n  !! no codec'd noise at {NOISE}")
        print("     run: python tools/wakeword/prepare_discord_corpus.py --background")
        print("     WITHOUT it there is no false-positive gate and no model is shippable.")

    # Decoded once and reused: the round trip is ~20ms a clip and every model sees the
    # identical audio, which is what makes the comparison paired.
    mic_pcm = [pcm_of(w) for w in mic]
    mic_opus = [through_discord(p) for p in mic_pcm]
    disc_pcm = [pcm_of(w) for w in disc]
    noise_pcm = [pcm_of(w) for w in noise]

    results = {}
    for name in args.models:
        model = load_model(name)
        if model is None:
            print(f"\n=== {name}: not found, skipping ===")
            continue
        print(f"\n=== {name} ===")
        print(f"  recall at threshold {args.threshold}")
        rec = {
            "mic": [peak(model, p) for p in mic_pcm],
            "mic->opus": [peak(model, p) for p in mic_opus],
            "discord": [peak(model, p) for p in disc_pcm],
        }
        for label, peaks in rec.items():
            report_recall(label, peaks, args.threshold)

        over = seen = 0
        clips_firing = 0
        for p in noise_pcm:
            o, s = frames_over(model, p, args.threshold)
            over += o
            seen += s
            clips_firing += bool(o)
        results[name] = {"recall": rec, "fp_frames": over, "fp_seen": seen,
                         "fp_clips": clips_firing}
        if seen:
            # ~12.5 frames a second at 80ms. Reported per hour to match
            # target_false_positives_per_hour in the training config.
            per_hour = over / (seen * 0.08) * 3600
            print(f"  FALSE POSITIVES on codec'd noise  <- THE GATE")
            print(f"    {over}/{seen} frames ({over / seen * 100:.3f}%), "
                  f"{clips_firing}/{len(noise_pcm)} clips, ~{per_hour:.1f}/hour")

    if len(results) > 1:
        print("\n=== verdict ===")
        base, *rest = list(results)
        for name in rest:
            b, n = results[base], results[name]
            for label in ("mic", "mic->opus", "discord"):
                bf = np.mean(np.array(b["recall"][label]) >= args.threshold) * 100
                nf = np.mean(np.array(n["recall"][label]) >= args.threshold) * 100
                print(f"  {label:<12} {base} {bf:5.1f}%  ->  {name} {nf:5.1f}%  "
                      f"({nf - bf:+.1f})")
            bfp = b["fp_frames"] / max(b["fp_seen"], 1)
            nfp = n["fp_frames"] / max(n["fp_seen"], 1)
            print(f"  {'false pos':<12} {base} {bfp*100:.3f}%  ->  "
                  f"{name} {nfp*100:.3f}%  ({(nfp - bfp)*100:+.3f})")
            if nfp > bfp * 2 and nfp > 0.001:
                print("\n  ** REJECT. It fires on codec'd noise far more than the")
                print("     baseline does. That is the codec-shortcut this was built to")
                print("     catch: recall on codec'd positives cannot redeem it, because")
                print("     both models run at once and the highest score wins - so its")
                print("     false positives would become everyone's.")


if __name__ == "__main__":
    main()
