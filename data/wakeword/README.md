# Wake-word models

`hey_pal.onnx` is trained by `tools/wakeword/train.py`; see
`tools/wakeword/hey_pal.yaml` for the configuration and the runner's docstring for the
eight compatibility shims openWakeWord's pipeline currently needs.

**Why a custom model exists at all.** None of openWakeWord's six pretrained models fire
on "hey pal". Measured across 60 real recordings:

| model | mean | max | fired (>0.5) |
|---|---|---|---|
| `alexa` | 0.052 | 0.587 | 2/60 |
| `hey_mycroft` | 0.000 | 0.000 | 0/60 |
| `hey_jarvis` | 0.005 | 0.070 | 0/60 |
| `hey_rhasspy` | 0.004 | 0.017 | 0/60 |

The phrase shape is wrong - those are all "hey + two syllables" or a three-syllable
name, and "hey pal" is "hey + one". No threshold recovers that; `hey_mycroft` scores a
literal zero.

**Measured result (2026-08-09, 30k synthetic samples, 50k steps).** Against the 236 A5
recordings that actually open with the phrase — the label comes from `prompts.json`, and
the 4 `control` clips that do not say it are scored separately:

| threshold | recall | non-wake-word clips firing |
|---|---|---|
| 0.1 | **91.9%** | 0/4 (max score 0.001) |
| 0.3 | 83.1% | 0/4 |
| 0.5 | 79.2% | 0/4 |

13 clips score below 0.05, so **94.5% is the ceiling** for any threshold — those are the
model failing to recognise the utterance at all, not near misses. That is what makes 0.1
the default: within this evidence lower is strictly better, and the gap to the highest
non-wake-word score is a factor of 100.

The negative evidence is thin — four clips, no continuous room chatter — so the false
positive side is genuinely unmeasured. It becomes measurable in use: a false positive
shows up in `/palintel status` as an activation with no transcript.

**Validate before trusting a model:**

    python tools/wakeword/evaluate.py --model hey_pal

Read the score distribution, not only the pass rate: a model scraping over threshold on
real audio while scoring 0.99 on synthetic has not transferred, it has been lucky.

Runtime needs none of the training dependencies - inference is this ~1MB file on CPU, at
a realtime factor near 0.015. Several models can run at once
(`voice.models = ["hey_jarvis", "hey_pal"]`), highest score wins, which is how a new
model's real-world recall gets attributed rather than inferred.
