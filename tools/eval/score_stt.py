"""Score the STT evaluation set across three defence layers (assumption A5).

Passes, run over identical audio so the difference is attributable to the layer alone:

  1. raw      - plain transcription. Does the model know the word at all?
  2. hotwords - the lexicon supplied as decoding hints (keyterm boosting).
  3. fuzzy    - phonetic + edit-distance repair applied to pass 1 output.

Reporting each separately answers a design question, not just an accuracy one: if fuzzy
alone reaches target, the boosting layer is complexity we can drop; if nothing reaches
95%, entity handling needs redesign before Phase 1 (ADR-0007 kill criterion).

Usage: python tools/eval/score_stt.py --condition noisy [--model small.en]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
EVAL = REPO / "data" / "stt_eval"
LEXICON = REPO / "data" / "1.0.2" / "lexicon.json"

MATCH_THRESHOLD = 0.78  # below this we decline rather than coerce - see ADR-0007


def phonetic(word: str) -> str:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return ""
    for a, b in (("ph", "f"), ("ck", "k"), ("qu", "kw"), ("x", "ks"),
                 ("gh", "g"), ("kn", "n"), ("wr", "r"), ("mb", "m")):
        w = w.replace(a, b)
    out = w[0].upper() + re.sub(r"[aeiou]", "", w[1:]).upper()
    return re.sub(r"(.)\1+", r"\1", out)


def load_lexicon() -> dict[str, list[str]]:
    """canonical -> [surface forms to match against], lowercased."""
    lex = json.loads(LEXICON.read_text(encoding="utf-8"))
    forms: dict[str, list[str]] = {}
    for p in lex["pals"]:
        forms[p["canonical"]] = [p["canonical"].lower()] + [a.lower() for a in p["aliases"]]
    for r in lex["resources"]:
        forms[r["canonical"]] = [r["canonical"].replace("_", " ").lower()] + \
                                [a.lower() for a in r["aliases"]]
    return forms


def repair(transcript: str, forms: dict[str, list[str]]) -> set[str]:
    """Fuzzy-match transcript n-grams to canonical entities.

    Matches below threshold are deliberately NOT coerced: answering confidently about
    the wrong entity is worse than admitting the miss.
    """
    words = re.findall(r"[a-z']+", transcript.lower())
    grams = [" ".join(words[i:i + n]) for n in (1, 2) for i in range(len(words) - n + 1)]
    found = set()
    for canon, surfaces in forms.items():
        best = 0.0
        cp = phonetic(canon)
        for g in grams:
            for s in surfaces:
                best = max(best, SequenceMatcher(None, g, s).ratio())
            if phonetic(g) == cp and cp:
                best = max(best, 0.95)
        if best >= MATCH_THRESHOLD:
            found.add(canon)
    return found


def literal_hits(transcript: str, expected: list[str]) -> set[str]:
    t = transcript.lower()
    return {e for e in expected if e.lower() in t}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True)
    ap.add_argument("--model", default="small.en",
                    help="faster-whisper model: tiny.en | base.en | small.en | medium.en")
    ap.add_argument("--device", default="cpu", choices=["cpu", "cuda", "auto"])
    args = ap.parse_args()

    try:
        import _cuda  # noqa: F401  registers NVIDIA DLL dirs on Windows
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("faster-whisper not installed:  pip install faster-whisper")

    src = EVAL / args.condition
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    forms = load_lexicon()
    hotwords = ", ".join(sorted({c for c in forms})[:400])

    print(f"model={args.model}  condition={args.condition}  clips={len(manifest)}")
    # device="auto" selects CUDA when a GPU is visible, which fails at encode time if
    # the cuBLAS runtime is absent. Try GPU, fall back to CPU: this eval is ~90s of
    # audio, so CPU is perfectly adequate and always available.
    # float16 on GPU, int8 on CPU: int8 exists to make CPU inference bearable and gives
    # up accuracy the GPU has no reason to sacrifice.
    ctype = "float16" if args.device == "cuda" else "int8"
    try:
        model = WhisperModel(args.model, device=args.device, compute_type=ctype)
        segs, _ = model.transcribe(
            str(src / next(iter(manifest.values()))["file"]), language="en")
        list(segs)  # force execution; CUDA errors surface at encode, not construction
    except Exception as e:
        if args.device == "cpu":
            raise
        print(f"  {args.device} unavailable ({type(e).__name__}: {e}) - falling back to CPU")
        model = WhisperModel(args.model, device="cpu", compute_type="int8")

    rows = []
    for pid, m in sorted(manifest.items()):
        wav = str(src / m["file"])

        def run(**kw) -> str:
            segs, _ = model.transcribe(wav, language="en", beam_size=5, **kw)
            return " ".join(s.text for s in segs).strip()

        raw = run()
        boosted = run(initial_prompt=f"Palworld pal names: {hotwords}")

        expected = set(m["expect_entities"])
        got_raw = literal_hits(raw, m["expect_entities"])
        got_boost = literal_hits(boosted, m["expect_entities"])
        # Layers are CUMULATIVE: production runs boosting, then fuzzy repair over its
        # output. Measuring repair against the raw transcript instead would understate
        # the real pipeline, since it throws away whatever boosting recovered.
        got_fuzzy_raw = repair(raw, forms) & expected
        got_pipeline = repair(boosted, forms) & expected

        rows.append({
            "id": pid, "group": m["group"], "expected": sorted(expected),
            "raw_text": raw, "boosted_text": boosted,
            "raw": len(got_raw), "boosted": len(got_boost),
            "fuzzy": len(got_fuzzy_raw), "pipeline": len(got_pipeline),
            "n": len(expected),
        })
        flag = "" if got_pipeline == expected else "  <-- MISS"
        print(f"  {pid} {m['group']:<9} raw={len(got_raw)} boost={len(got_boost)} "
              f"fuzzy={len(got_fuzzy_raw)} PIPE={len(got_pipeline)}/{len(expected)}{flag}"
              f"\n       raw:   {raw[:64]}\n       boost: {boosted[:64]}")

    def rate(key: str, group: str | None = None) -> float:
        sel = [r for r in rows if group is None or r["group"] == group]
        tot = sum(r["n"] for r in sel)
        return (sum(r[key] for r in sel) / tot * 100) if tot else 0.0

    print("\n" + "=" * 74)
    print(f"{'group':<12}{'raw':>10}{'+hotwords':>12}{'fuzzy(raw)':>13}{'PIPELINE':>12}")
    for g in ("control", "hard", "utterance", None):
        label = g or "ALL"
        print(f"{label:<12}{rate('raw', g):>9.1f}%{rate('boosted', g):>11.1f}%"
              f"{rate('fuzzy', g):>12.1f}%{rate('pipeline', g):>11.1f}%")
    print(f"\nPIPELINE = hotwords + fuzzy repair, i.e. what production actually runs.")
    print(f"utterance row is the realistic condition; bare names are a lower bound.")

    overall = rate("pipeline")
    print("=" * 62)
    print(f"\nA5 target: >=95% entity accuracy.  Achieved: {overall:.1f}%  "
          f"-> {'PASS' if overall >= 95 else 'FAIL'}")
    if overall < 95:
        print("Per ADR-0007 this means entity handling needs redesign before Phase 1.")
        print("Misses below are the alias candidates to fold into the lexicon.")

    (src / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\ndetail -> {src / 'results.json'}")


if __name__ == "__main__":
    main()
