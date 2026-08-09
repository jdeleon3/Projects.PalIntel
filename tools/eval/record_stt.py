"""Record the STT evaluation set (assumption A5).

Records once; scoring replays the audio as many times as needed against different
models and settings, so no take is ever wasted.

Each run is tagged with a CONDITION. Record the same prompts under more than one
condition - the comparison is what makes a failure diagnosable:

  quiet  - isolates vocabulary difficulty. Failures here mean the model does not know
           the word, and no amount of noise handling will help.
  noisy  - the real deployment condition (game audio, party chatter). Failures only
           here point at VAD, mic setup, or gain instead.

Usage:
    python tools/eval/record_stt.py --condition noisy
    python tools/eval/record_stt.py --condition quiet --only P05,P06

Controls: ENTER starts a take, ENTER stops it. "r" redoes the previous prompt,
"s" skips, "q" quits (progress is saved).
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "data" / "stt_eval"
SAMPLE_RATE = 16_000  # what speech models expect; higher buys nothing here
CHANNELS = 1


def record_until_enter(sd, q: queue.Queue) -> bytes:
    """Capture 16-bit mono PCM until the user presses ENTER."""
    frames = bytearray()
    with sd.RawInputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
                           callback=lambda indata, *_: q.put(bytes(indata))):
        input()
        while not q.empty():
            frames += q.get()
    return bytes(frames)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True,
                    help="recording condition, e.g. quiet | noisy | ingame")
    ap.add_argument("--only", help="comma-separated prompt ids to record")
    ap.add_argument("--batch", type=int,
                    help="record only this batch. The set is collected over several "
                         "sittings, and each batch is a balanced sample on its own, so "
                         "stopping after any batch still leaves an unbiased set.")
    args = ap.parse_args()

    try:
        import sounddevice as sd
    except ImportError:
        sys.exit("sounddevice not installed:  pip install sounddevice")

    prompts = json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]
    if args.batch is not None:
        prompts = [p for p in prompts if p.get("batch") == args.batch]
        if not prompts:
            sys.exit(f"no prompts in batch {args.batch}")
    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",")}
        prompts = [p for p in prompts if p["id"] in wanted]

    dest = EVAL / args.condition
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})

    print(f"device: {sd.query_devices(kind='input')['name']}")
    print(f"condition: {args.condition}   prompts: {len(prompts)}")
    print("\nENTER starts, ENTER stops.  r = redo,  s = skip,  q = quit\n")

    q: queue.Queue = queue.Queue()
    i = 0
    while i < len(prompts):
        p = prompts[i]
        if p["id"] in manifest:
            print(f"  {p['id']} already recorded - skipping (delete from manifest to redo)")
            i += 1
            continue

        print(f"[{i + 1}/{len(prompts)}]  {p['id']}  ({p['group']})")
        print(f"    SAY:  {p['text']}")
        cmd = input("    ENTER to record > ").strip().lower()
        if cmd == "q":
            break
        if cmd == "s":
            i += 1
            continue
        if cmd == "r":
            i = max(0, i - 1)
            manifest.pop(prompts[i]["id"], None)
            continue

        print("    recording... ENTER to stop")
        audio = record_until_enter(sd, q)
        secs = len(audio) / (SAMPLE_RATE * 2 * CHANNELS)

        wav = dest / f"{p['id']}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(audio)

        manifest[p["id"]] = {
            "file": wav.name,
            "text": p["text"],
            "group": p["group"],
            "expect_entities": p["expect_entities"],
            "condition": args.condition,
            "duration_s": round(secs, 2),
            "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"    saved {wav.name}  ({secs:.1f}s)\n")
        i += 1

    print(f"\n{len(manifest)}/{len(prompts)} recorded -> {dest}")
    if len(manifest) < len(prompts):
        print("re-run the same command to continue where you left off")


if __name__ == "__main__":
    main()
