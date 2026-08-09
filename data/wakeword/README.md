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

**Validate before trusting a model:**

    python tools/wakeword/evaluate.py --model hey_pal

Recall is measured against the 240 A5 recordings. Read the score distribution, not only
the pass rate: a model scraping over threshold on real audio while scoring 0.99 on
synthetic has not transferred, it has been lucky.

Runtime needs none of the training dependencies - inference is this ~1MB file on CPU, at
a realtime factor near 0.015. Several models can run at once
(`voice.models = ["hey_jarvis", "hey_pal"]`), highest score wins, which is how a new
model's real-world recall gets attributed rather than inferred.
