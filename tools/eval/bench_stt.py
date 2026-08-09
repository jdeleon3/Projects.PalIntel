"""Benchmark STT models on accuracy AND latency together (A5 + latency budget).

Accuracy alone cannot pick the model. The end-to-end budget is p95 <= 2.5s
(Docs/01-architecture.md section 7), of which STT was allotted ~300ms. A model that is
more accurate but takes two seconds fails the product even while winning the benchmark,
so both are measured on the same clips and reported side by side.

Runs the production path only (hotwords + fuzzy repair) - the cumulative pipeline.

Usage: python tools/eval/bench_stt.py --condition noisy
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from score_stt import load_lexicon, repair  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "data" / "stt_eval"

MODELS = ["tiny.en", "base.en", "small.en", "medium.en"]
STT_BUDGET_MS = 300  # allotted share of the 2.5s end-to-end target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True)
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    args = ap.parse_args()

    import _cuda  # noqa: F401  registers NVIDIA DLL dirs on Windows
    from faster_whisper import WhisperModel

    src = EVAL / args.condition
    manifest_path = src / "manifest.json"
    if not manifest_path.exists():
        have = sorted(p.name for p in EVAL.iterdir()
                      if p.is_dir() and (p / "manifest.json").exists())
        raise SystemExit(
            f"No recordings for condition '{args.condition}'.\n"
            f"  Record them first:\n"
            f"    python tools/eval/record_stt.py --condition {args.condition}\n"
            f"  Conditions already recorded: {', '.join(have) if have else '(none)'}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    forms = load_lexicon()
    hotwords = ", ".join(sorted(forms)[:400])
    audio_s = sum(m["duration_s"] for m in manifest.values())

    print(f"condition={args.condition}  clips={len(manifest)}  audio={audio_s:.0f}s\n")
    results = []

    for name in args.models.split(","):
        name = name.strip()
        print(f"-- {name}")
        ctype = "float16" if args.device == "cuda" else "int8"
        try:
            model = WhisperModel(name, device=args.device, compute_type=ctype)
        except Exception as e:
            print(f"   load failed: {e}")
            continue

        hits = total = 0
        times = []
        for pid, m in sorted(manifest.items()):
            t0 = time.perf_counter()
            segs, _ = model.transcribe(str(src / m["file"]), language="en", beam_size=5,
                                       initial_prompt=f"Palworld pal names: {hotwords}")
            text = " ".join(s.text for s in segs).strip()
            times.append((time.perf_counter() - t0) * 1000)
            expected = set(m["expect_entities"])
            if expected:
                hits += len(repair(text, forms) & expected)
                total += len(expected)

        acc = hits / total * 100 if total else 0.0
        med, p95 = st.median(times), sorted(times)[int(len(times) * 0.95) - 1]
        rtf = (sum(times) / 1000) / audio_s
        results.append((name, acc, med, p95, rtf))
        print(f"   accuracy {acc:5.1f}%   median {med:7.0f}ms   p95 {p95:7.0f}ms   RTF {rtf:.2f}")

    print("\n" + "=" * 72)
    print(f"{'model':<12}{'accuracy':>10}{'median':>10}{'p95':>10}{'RTF':>8}   verdict")
    print("-" * 72)
    for name, acc, med, p95, rtf in results:
        ok = p95 <= STT_BUDGET_MS
        verdict = "within budget" if ok else f"{p95 / STT_BUDGET_MS:.1f}x over budget"
        print(f"{name:<12}{acc:>9.1f}%{med:>9.0f}ms{p95:>9.0f}ms{rtf:>8.2f}   {verdict}")
    print("=" * 72)
    print(f"STT budget: {STT_BUDGET_MS}ms p95. RTF < 1.0 means faster than real time.")
    print(f"Device: {args.device} ({'float16' if args.device == 'cuda' else 'int8'}).")
    if args.device == "cuda":
        try:
            import subprocess
            q = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,utilization.gpu",
                 "--format=csv,noheader"], capture_output=True, text=True, timeout=10)
            print(f"GPU at end of run: {q.stdout.strip()}")
        except Exception:
            pass
        print("GPU contention matters: record whether the game was running AND under")
        print("load. A menu screen is not a worst case for frame-time impact.")

    (src / "bench.json").write_text(json.dumps(
        [{"model": n, "accuracy": a, "median_ms": m, "p95_ms": p, "rtf": r}
         for n, a, m, p, r in results], indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
