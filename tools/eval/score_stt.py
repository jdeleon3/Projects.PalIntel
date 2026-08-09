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


def threshold_for(target: str) -> float:
    """Short targets need a higher bar.

    Similarity is length-sensitive: "Mau" scores 0.80 against "my", and "ore" against
    "for", because a one-character difference is proportionally huge on a 3-letter
    string. A single global threshold therefore either admits junk on short names or
    rejects valid matches on long ones. Scaling with length lets long invented names
    stay permissive while short ones demand near-exact agreement.
    """
    n = len(re.sub(r"[^a-z]", "", target.lower()))
    if n <= 3:
        return 1.0    # exact only
    if n <= 5:
        return 0.90
    return MATCH_THRESHOLD


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


def squash(s: str) -> str:
    """Strip everything that only exists because of ASR tokenisation.

    Whisper renders one invented word as several English ones - "Leezpunk" becomes
    "Lee's bunk", "Mycora" becomes "my Korra". Comparing across that split with the
    spaces and apostrophes still in place drags the similarity score below threshold
    even when the letters line up almost exactly ("lee's bunk" vs "leezpunk" scores
    0.66; "leesbunk" vs "leezpunk" scores 0.88).

    This is a fix for a known artifact class, not for particular clips.
    """
    return re.sub(r"[^a-z]", "", s.lower())


def repair(transcript: str, forms: dict[str, list[str]]) -> set[str]:
    """Fuzzy-match transcript n-grams to canonical entities.

    Matches below threshold are deliberately NOT coerced: answering confidently about
    the wrong entity is worse than admitting the miss (ADR-0007).
    """
    words = re.findall(r"[a-z']+", transcript.lower())
    # 3-grams included because word-splitting can produce three tokens
    # ("the nurse I grew down"), not just two.
    grams = [" ".join(words[i:i + n])
             for n in (1, 2, 3) for i in range(len(words) - n + 1)]
    squashed = [(g, squash(g)) for g in grams]

    found = set()
    for canon, surfaces in forms.items():
        cp = phonetic(canon)
        sq_surfaces = [(s, squash(s)) for s in surfaces]
        for g, gs in squashed:
            if not gs:
                continue
            hit = False
            for s, ss in sq_surfaces:
                need = threshold_for(s)
                score = max(SequenceMatcher(None, g, s).ratio(),
                            SequenceMatcher(None, gs, ss).ratio())
                if score >= need:
                    hit = True
                    break
            # Phonetic agreement is only trusted on targets long enough for the key to
            # carry information; three-letter keys collide constantly.
            if not hit and cp and len(cp) >= 4 and phonetic(gs) == cp:
                hit = True
            if hit:
                found.add(canon)
                break
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
    manifest_path = src / "manifest.json"
    if not manifest_path.exists():
        have = sorted(p.name for p in EVAL.iterdir()
                      if p.is_dir() and (p / "manifest.json").exists())
        sys.exit(
            f"No recordings for condition '{args.condition}'.\n"
            f"  Record them first:\n"
            f"    python tools/eval/record_stt.py --condition {args.condition}\n"
            f"  Conditions already recorded: {', '.join(have) if have else '(none)'}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        # `hotwords` is faster-whisper's actual keyterm-biasing mechanism.
        # `initial_prompt` is a context hint and was measurably HURTING: it dropped the
        # control group from 75% to 50% by steering the model toward a general context.
        boosted = run(hotwords=hotwords)

        expected = set(m["expect_entities"])
        got_raw = literal_hits(raw, m["expect_entities"])
        got_boost = literal_hits(boosted, m["expect_entities"])
        # Layers are CUMULATIVE: production runs boosting, then fuzzy repair over its
        # output. Measuring repair against the raw transcript instead would understate
        # the real pipeline, since it throws away whatever boosting recovered.
        got_fuzzy_raw = repair(raw, forms) & expected
        all_found = repair(boosted, forms)
        got_pipeline = all_found & expected
        # Precision matters as much as recall here and was previously unmeasured.
        # Loosening the matcher trades misses for WRONG entities, and ADR-0007 treats a
        # confident wrong answer as worse than an admitted miss - so a recall gain that
        # comes with a spurious-match spike is a regression, not an improvement.
        spurious = sorted(all_found - expected)

        rows.append({
            "id": pid, "group": m["group"], "expected": sorted(expected),
            "raw_text": raw, "boosted_text": boosted,
            "raw": len(got_raw), "boosted": len(got_boost),
            "fuzzy": len(got_fuzzy_raw), "pipeline": len(got_pipeline),
            "spurious": spurious, "n": len(expected),
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

    total_sp = sum(len(r["spurious"]) for r in rows)
    clips_sp = sum(1 for r in rows if r["spurious"])
    print(f"\nSPURIOUS matches: {total_sp} across {clips_sp}/{len(rows)} clips "
          f"(entities found that were not spoken)")
    if total_sp:
        worst = sorted((r for r in rows if r["spurious"]),
                       key=lambda r: -len(r["spurious"]))[:5]
        for r in worst:
            print(f"   {r['id']} expected={r['expected']} -> also matched {r['spurious'][:4]}")

    overall = rate("pipeline")
    print("=" * 74)
    print(f"\nA5 target: >=95% entity accuracy.  Achieved: {overall:.1f}%  "
          f"-> {'PASS' if overall >= 95 else 'FAIL'}")
    if overall < 95:
        print("Per ADR-0007 this means entity handling needs redesign before Phase 1.")
        print("Misses below are the alias candidates to fold into the lexicon.")

    (src / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"\ndetail -> {src / 'results.json'}")


if __name__ == "__main__":
    main()
