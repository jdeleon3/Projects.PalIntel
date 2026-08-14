"""Build the Discord-path training corpus for a second `hey_pal` model.

Takes the corpus the shipping model was trained on and puts it through the codec the
deployment path actually uses (`discord_path`), so a second model can be trained on what
the bot will really hear. The two run side by side — `voice.models` is a list, inference
is CPU-bound at a realtime factor near 0.015, and the highest score wins — so this
specialises rather than replaces.

**The trap this is built around, and it would not have announced itself.** If the
positives go through Opus and the negatives do not, "codec artifacts" become a perfect
predictor of the positive class. The model would score beautifully on codec'd positives
and fire on *anything* arriving over Discord — which is the failure `hey_pal.yaml` already
names as the one that makes a wake word unusable rather than merely imperfect, because it
is the failure the whole channel sees.

So **both** classes go through the same path at the same bitrate. The 30,000 adversarial
negatives — "hey pale", "hey paul", "hey gal" — are exactly the hard cases the model has to
keep rejecting, and they are codec'd too, so the codec cannot distinguish them from a
positive.

**One residual, stated rather than hidden.** The 2,000 hours of ACAV100M negatives are
precomputed *features*, not audio, so they cannot be round-tripped and stay clean. The
model can therefore still learn "codec ⇒ not ACAV", which is weaker than "codec ⇒ wake
word" but is not nothing. That is what `--background` is for: it builds a codec'd
false-positive set out of the ESC-50 clips, so the new model can be measured on codec'd
audio that contains no wake word at all. **Treat that measurement as the gate.** A model
that fires more on codec'd noise than the shipping one does on clean noise has learned the
shortcut, whatever its recall looks like.

    python tools/wakeword/prepare_discord_corpus.py --dry-run
    python tools/wakeword/prepare_discord_corpus.py
    python tools/wakeword/prepare_discord_corpus.py --background
"""
from __future__ import annotations

import argparse
import random
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discord_path import WAKE_RATE, read_wav, through_discord, write_wav  # noqa: E402

OWW = Path("C:/Users/jd02_/tools/owwtrain")
SRC = OWW / "out" / "hey_pal"
DST = OWW / "out" / "hey_pal_discord"
BACKGROUND = OWW / "data" / "background"
FP_SET = OWW / "data" / "background_discord"

# Both classes. Codec'ing only the positives is the shortcut this whole module is
# arranged to avoid - see the docstring.
SETS = ("positive_train", "positive_test", "negative_train", "negative_test")


def convert(src: Path, dst: Path, bitrate: int, loss: float, seed: int,
            limit: int = 0) -> int:
    clips = sorted(src.glob("*.wav"))
    if limit:
        clips = clips[:limit]
    if not clips:
        print(f"  {src.name}: nothing to do")
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    # Seeded per directory so a rerun reproduces the same corpus. Training data that
    # changes between runs makes every comparison against it meaningless.
    rng = random.Random(seed)
    t0 = time.time()
    for n, clip in enumerate(clips, 1):
        pcm, rate = read_wav(clip)
        if rate != WAKE_RATE:
            # Piper's libritts_r voice synthesises at 22,050 Hz. Resampled here rather
            # than left for the augmentation step, which asserts 16 kHz and would fail
            # 30,000 files at once - after writing a feature file that looks valid.
            from scipy.signal import resample_poly
            pcm = np.clip(resample_poly(pcm.astype(np.float32), WAKE_RATE, rate),
                          -32768, 32767).astype(np.int16)
        write_wav(dst / clip.name, through_discord(pcm, bitrate=bitrate, loss=loss,
                                                   rng=rng))
        if n % 2500 == 0:
            rate_s = n / max(time.time() - t0, 1e-6)
            print(f"    {n}/{len(clips)}  ({rate_s:.0f}/s, "
                  f"{(len(clips) - n) / rate_s / 60:.1f} min left)")
    print(f"  {src.name}: {len(clips)} clips in {(time.time() - t0) / 60:.1f} min")
    return len(clips)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bitrate", type=int, default=64,
                    help="kbps; 64 is Discord's default channel bitrate")
    ap.add_argument("--loss", type=float, default=0.0,
                    help="packet loss 0..1; uncalibrated, see discord_path")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0, help="clips per set, for a smoke run")
    ap.add_argument("--background", action="store_true",
                    help="also build the codec'd false-positive set from ESC-50")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SRC.is_dir():
        sys.exit(f"no source corpus at {SRC} - run the original generation first")

    print(f"source {SRC}\ntarget {DST}\n{args.bitrate} kbps, loss {args.loss:.0%}, "
          f"seed {args.seed}\n")
    total = 0
    for name in SETS:
        d = SRC / name
        n = len(sorted(d.glob("*.wav"))) if d.is_dir() else 0
        total += min(n, args.limit) if args.limit else n
        print(f"  {name:<16} {n:>6} clips")
    print(f"\n  {'total':<16} {total:>6} clips  "
          f"~{total * 0.0196 / 60:.0f} min\n")

    if args.dry_run:
        print("dry run - nothing written")
        return

    for i, name in enumerate(SETS):
        d = SRC / name
        if d.is_dir():
            # A different seed per set, so packet loss does not land on the same clip
            # index in positives and negatives and become a correlated artefact.
            convert(d, DST / name, args.bitrate, args.loss, args.seed + i, args.limit)

    if args.background:
        print("\nfalse-positive set (ESC-50 through the same path):")
        convert(BACKGROUND, FP_SET, args.bitrate, args.loss, args.seed + 99, args.limit)
        print(f"\n  -> {FP_SET}")
        print("  Measure BOTH models on this before shipping. A new model that fires")
        print("  more on codec'd noise than the old one does on clean noise has learned")
        print("  the codec, not the phrase.")


if __name__ == "__main__":
    main()
