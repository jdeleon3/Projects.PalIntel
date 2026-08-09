"""Record the STT evaluation set (assumption A5).

Records once; scoring replays the audio as many times as needed against different
models and settings, so no take is ever wasted.

Each run is tagged with a CONDITION. Record the same prompts under more than one
condition - the comparison is what makes a failure diagnosable:

  quiet  - isolates vocabulary difficulty. Failures here mean the model does not know
           the word, and no amount of noise handling will help.
  noisy  - the real deployment condition (game audio, party chatter). Failures only
           here point at VAD, mic setup, or gain instead.

The set is collected a batch at a time (see extend_stt_prompts.py). With no --batch,
this records the next batch that still has anything left, so repeating the same command
walks the set forward one sitting per run. Progress is derived from the manifest rather
than tracked in a separate file - the manifest is what proves a take exists, so the two
cannot drift apart.

Usage:
    python tools/eval/record_stt.py --condition quiet             # next unrecorded batch
    python tools/eval/record_stt.py --condition quiet --status    # progress, then exit
    python tools/eval/record_stt.py --condition quiet --batch 7   # a specific batch
    python tools/eval/record_stt.py --condition quiet --only P05,P06
    python tools/eval/record_stt.py --condition noisy --all       # ignore batching

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
                    help="record this batch. Defaults to the next one with anything "
                         "left to record. Each batch is a balanced sample on its own, "
                         "so stopping after any batch still leaves an unbiased set.")
    ap.add_argument("--all", action="store_true",
                    help="ignore batching and queue every unrecorded prompt")
    ap.add_argument("--status", action="store_true",
                    help="show per-batch progress and exit")
    args = ap.parse_args()

    prompts = json.loads((EVAL / "prompts.json").read_text(encoding="utf-8"))["prompts"]

    dest = EVAL / args.condition
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "manifest.json"
    manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists() else {})

    # Progress is derived from the manifest rather than tracked separately: the manifest
    # is what actually proves a take exists, so the two cannot disagree.
    batches: dict[int, list[dict]] = {}
    for p in prompts:
        batches.setdefault(p.get("batch", 0), []).append(p)
    done = {b: sum(1 for p in ps if p["id"] in manifest) for b, ps in batches.items()}
    unfinished = sorted(b for b, ps in batches.items() if done[b] < len(ps))

    if args.status:
        for b in sorted(batches):
            n, d = len(batches[b]), done[b]
            state = "complete" if d == n else ("in progress" if d else "not started")
            print(f"  batch {b:>2}  {d:>3}/{n:<3} {state}")
        left = sum(len(batches[b]) - done[b] for b in batches)
        print(f"\n  {len(prompts) - left}/{len(prompts)} recorded"
              f"{f'; next: batch {unfinished[0]}' if unfinished else '; all done'}")
        return

    try:
        import sounddevice as sd
    except ImportError:
        sys.exit("sounddevice not installed:  pip install sounddevice")

    if args.only:
        wanted = {s.strip().upper() for s in args.only.split(",")}
        prompts = [p for p in prompts if p["id"] in wanted]
    elif not args.all:
        batch = args.batch
        if batch is None:
            if not unfinished:
                print(f"all {len(prompts)} prompts recorded for '{args.condition}'.")
                return
            batch = unfinished[0]
            print(f"next unrecorded batch: {batch}")
        if batch not in batches:
            sys.exit(f"no prompts in batch {batch}")
        prompts = batches[batch]

    todo = [p for p in prompts if p["id"] not in manifest]
    print(f"device: {sd.query_devices(kind='input')['name']}")
    print(f"condition: {args.condition}   to record: {len(todo)} of {len(prompts)}")
    if not todo:
        print("nothing left in this selection.")
        return
    print("\nENTER starts, ENTER stops.  r = redo,  s = skip,  q = quit\n")

    # Only this sitting's prompts. "r" steps back and drops that id from the manifest,
    # so walking the full batch would let a redo at the first prompt delete a take from
    # an earlier session.
    prompts = todo

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
