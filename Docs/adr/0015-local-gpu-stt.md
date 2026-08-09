# ADR-0015 — Local GPU speech-to-text (faster-whisper `medium.en`)

**Status:** Accepted
**Reinforces:** [ADR-0003](0003-local-first-process.md)
**Supersedes:** `HighLevel.txt` §2 (Deepgram / OpenAI Whisper row)
**Evidence:** Phase 0.6 benchmark, `data/stt_eval/noisy_v1/bench.json`

## Context

Speech-to-text sits in the hot path and was the last unresolved component. The original
sketch specified a hosted provider (Deepgram or the OpenAI Whisper API). Local inference
was chosen for evaluation because it costs nothing per query, keeps audio on the machine,
and is the Phase 5 endgame anyway.

**The first benchmark said local was not viable.** On CPU (int8):

| model | accuracy | p95 | RTF |
|---|---|---|---|
| tiny.en | 68% | 830 ms | 0.11 |
| base.en | 72% | 519 ms | 0.19 |
| small.en | 76% | 4,388 ms | 0.59 |
| medium.en | 84% | 4,779 ms | **1.35** |

`medium.en` — the only model with usable accuracy — had **RTF 1.35: transcription took
longer than the audio itself.** Every model missed the 300 ms budget, and the conclusion
drawn was that STT had to move to a hosted provider.

**That conclusion was wrong, and the cause was a configuration fault rather than a real
constraint.** CUDA initialisation was failing with `cublas64_12.dll is not found`, so the
benchmark silently ran on CPU. The GPU (RTX 5080, compute capability 12.0) was present and
CTranslate2 already detected it; only the cuBLAS/cuDNN runtime libraries were missing.

## Decision

**faster-whisper `medium.en`, float16, on the local GPU.**

Measured on the same audio, with Palworld running:

| | idle | game running |
|---|---|---|
| median | 141 ms | **171 ms** |
| p95 | 210 ms | **295 ms** |
| accuracy | 88% | 88% |
| VRAM | — | **~930 MiB** |

A 23× speedup over CPU *and* higher accuracy, because the GPU runs float16 rather than the
int8 quantisation CPU needed to be even marginally usable.

GPU contention was observed and judged acceptable: no perceptible frame impact reported
during play, and VRAM headroom remained ~8.9 GB.

`large-v3` was tested and rejected — **less** accurate than `medium.en` (80% vs 88%) while
being slower. The `.en` models are English-specialised; bigger is not better here.

### The CUDA runtime gotcha

On Windows, `os.add_dll_directory()` alone is **not** sufficient. CTranslate2's native
extension resolves cuBLAS through the ordinary search order, which consults `PATH`.
Registering only the DLL directory succeeds silently and still fails at encode time — a
genuinely misleading failure mode that cost a full wrong conclusion. Both mechanisms are
applied in `tools/eval/_cuda.py`.

## Alternatives considered

| Alternative | Rejected because |
|---|---|
| **Deepgram (hosted)** | Genuinely strong: true keyterm boosting rather than Whisper's `initial_prompt` hint, streaming, ~300 ms. But it costs per query, sends audio off-machine, and adds a network dependency to the hot path — all unnecessary once local GPU met the budget. Remains the fallback if GPU contention proves unacceptable under heavier load. |
| **CPU inference** | RTF 1.35 at usable accuracy. Not viable at any model size. |
| **`base.en` on GPU** | 79 ms p95, roughly half the GPU burst, but 68% accuracy vs 88%. Held as the lever if frame-time impact appears under heavy load. |
| **`large-v3`** | Slower *and* less accurate on this domain. |

## Consequences

**Positive**
- Zero per-query STT cost; audio never leaves the machine, strengthening the privacy
  posture in [01-architecture.md](../01-architecture.md) §9
- No network dependency in the hot path for STT
- Comfortably inside the latency budget at moderate game load
- Moves the system materially closer to fully offline (Phase 5)

**Negative**
- Requires a capable GPU. This is reasonable for a Palworld machine but makes the tool
  non-portable to low-spec setups, where the Deepgram path would need reinstating.
- Adds a CUDA runtime dependency (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`) with a
  confusing failure mode when misconfigured.
- **Contention is only tested at moderate game load.** GPU utilisation read 20% before the
  run; a heavy combat scene is not covered. p95 sat at 295 ms against a 300 ms budget, so
  there is almost no headroom for worse contention.
- `initial_prompt` is a weaker boosting mechanism than Deepgram keyterms, and was observed
  *degrading* some transcriptions. Its value should be re-checked on the v2 eval set.

**Neutral**
- The 300 ms figure is an internal allocation within the 2.5 s end-to-end target, not an
  external constraint. Drifting to ~400 ms under load would not be user-visible; the real
  risk is frame-time impact on the game, not the latency number.
