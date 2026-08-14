"""Put audio through the Discord voice path, offline.

The shipping `hey_pal` model was trained on synthetic TTS with room augmentation and
validated against 240 recordings from the **local microphone**. Since 2026-08-13 the
deployment path is Discord receive, and that is a different signal: 48 kHz stereo, Opus at
the channel's bitrate, packets that sometimes do not arrive.

Measured on the captured clips before building any of this — and stated as evidence rather
than proof, because it has a selection bias described below:

    path                    n    median   mean    p10     under 0.3
    mic     (08-11, 08-12)  96   0.955    0.851   0.567   9  (9.4%)
    discord (08-13)         42   0.895    0.726   0.197   7  (16.7%)

The median barely moves; the **bottom decile collapses** from 0.567 to 0.197 against a
0.1 firing threshold. **The bias:** a captured clip exists only because the wake word
fired, so recall over them is 100% by construction and the distribution above is
conditioned on having fired. It is a reason to look, not a result.

**This uses py-cord's own libopus**, which is the same library and the same version the
bot's voice path already links, so the codec here is the codec in production rather than
an approximation of it.

**What it does not simulate: Krisp, echo cancellation and AGC.** Those run on the
*sending* client before encoding, so they vary per speaker and cannot be reproduced from
this side. The DAVE work found Krisp aggressive enough to suppress pure tones outright, so
this is a real gap and not a rounding error — which is exactly why a model trained on this
still has to be validated against audio recorded through a real Discord client.

    python tools/wakeword/discord_path.py --in clips/ --out clips_discord/
    python tools/wakeword/discord_path.py --in clips/ --out out/ --loss 0.02
"""
from __future__ import annotations

import argparse
import random
import sys
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

# Discord voice: 48 kHz stereo, 20 ms frames. These are py-cord's own Encoder constants
# rather than numbers typed here, so they cannot drift from what the bot actually uses.
WAKE_RATE = 16_000          # openWakeWord's native rate, and what the sink hands us


def _codec(bitrate: int, loss: float):
    """An encoder/decoder pair configured the way a Discord client sends.

    `fec` and `expected_packet_loss` are py-cord's defaults for its own sending encoder.
    They matter when `loss` is non-zero: FEC is what lets the decoder reconstruct rather
    than merely conceal, and turning it off would model a worse network than Discord's.
    """
    import discord.opus as opus

    if not opus.is_loaded():
        opus._load_default()
    enc = opus.Encoder(application="audio", bitrate=bitrate, fec=True,
                       expected_packet_loss=max(loss, 0.01), bandwidth="full",
                       signal_type="voice")
    dec = opus.Decoder()
    return enc, dec


def through_discord(pcm: np.ndarray, bitrate: int = 64, loss: float = 0.0,
                    rng: random.Random | None = None) -> np.ndarray:
    """16 kHz mono in, 16 kHz mono out, having been Opus round-tripped at 48 kHz stereo.

    `bitrate` is kbps and defaults to **64**, which is Discord's default voice channel
    bitrate. A boosted server can be higher; the sender's client picks it, so this is the
    conservative case rather than the only one.

    `loss` drops whole packets. The decoder is asked to conceal them, which is what the
    real receive path does - `discord_voice` counts those as `concealed`. Left at 0 by
    default and **uncalibrated**: no real session has produced a non-zero loss figure yet,
    so a number here would be invented.
    """
    rng = rng or random.Random(0)
    enc, dec = _codec(bitrate, loss)

    # 16k mono -> 48k stereo. resample_poly is a proper polyphase filter; naive
    # interpolation would add aliasing that is not part of the path being modelled and
    # would show up as codec damage that Discord does not actually do.
    up = resample_poly(pcm.astype(np.float32), 3, 1)
    stereo = np.repeat(up[:, None], 2, axis=1).astype(np.int16)

    frame = enc.SAMPLES_PER_FRAME                    # 960 samples = 20 ms at 48 kHz
    out = []
    for i in range(0, len(stereo) - frame + 1, frame):
        block = stereo[i:i + frame]
        packet = enc.encode(block.tobytes(), frame)
        if loss and rng.random() < loss:
            # Packet lost. Ask the decoder to conceal, exactly as the receive path does.
            pcm_out = dec.decode(None, fec=False)
        else:
            pcm_out = dec.decode(packet)
        out.append(np.frombuffer(pcm_out, dtype=np.int16).reshape(-1, 2))

    if not out:
        return pcm.astype(np.int16)
    decoded = np.concatenate(out)
    mono = decoded.astype(np.float32).mean(axis=1)   # the two channels are identical
    down = resample_poly(mono, 1, 3)
    return np.clip(down, -32768, 32767).astype(np.int16)


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as f:
        if f.getsampwidth() != 2:
            raise ValueError(f"{path.name}: expected 16-bit PCM")
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
        if f.getnchannels() == 2:
            pcm = pcm.reshape(-1, 2).mean(axis=1).astype(np.int16)
        return pcm, f.getframerate()


def write_wav(path: Path, pcm: np.ndarray, rate: int = WAKE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description="Opus round-trip a directory of WAVs")
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", dest="dst", type=Path, required=True)
    ap.add_argument("--bitrate", type=int, default=64, help="kbps (Discord default 64)")
    ap.add_argument("--loss", type=float, default=0.0, help="packet loss 0..1")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    clips = sorted(args.src.rglob("*.wav"))
    if args.limit:
        clips = clips[:args.limit]
    if not clips:
        sys.exit(f"no .wav under {args.src}")

    rng = random.Random(args.seed)
    print(f"{len(clips)} clips  ->  {args.dst}   "
          f"({args.bitrate} kbps, loss {args.loss:.0%})")
    for n, clip in enumerate(clips, 1):
        pcm, rate = read_wav(clip)
        if rate != WAKE_RATE:
            pcm = resample_poly(pcm.astype(np.float32), WAKE_RATE, rate)
            pcm = np.clip(pcm, -32768, 32767).astype(np.int16)
        out = through_discord(pcm, bitrate=args.bitrate, loss=args.loss, rng=rng)
        write_wav(args.dst / clip.relative_to(args.src), out)
        if n % 500 == 0 or n == len(clips):
            print(f"  {n}/{len(clips)}")


if __name__ == "__main__":
    main()
