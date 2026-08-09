"""Wake-word activation — deciding that an utterance was addressed to the assistant.

**Matched fuzzily, because the wake word is mangled as reliably as the Pal names are.**
Across 236 recorded utterances the phrase *"Hey Pal"* was transcribed cleanly 214 times
and wrongly 22 times (9.3%): `hippel`, `hippow`, `hapell`, `apel`, `ippel`, `apal`,
`hey power`, `hippal`. An exact match silently rejects one activation in eleven, and a
false negative here is the worst failure mode in the system - the player speaks, nothing
happens, and there is nothing to diagnose from ([ADR-0004](../Docs/adr/0004-wake-word-activation.md)
consequences).

The same squash/phonetic machinery that repairs entity names repairs this, for the same
reason: STT splits one invented word into several English ones ("Hey Pal" → "Hapella's"),
and comparing across that split with spaces intact drops similarity below any usable bar.

**This is not sufficient as the only gate, and the measurement says so.** Scored against
236 real utterances and 22 lines of plausible party chatter:

    threshold   recall   fires on chatter
      0.30      100.0%       12 / 22
      0.50       95.8%        8 / 22
      0.62       92.8%        6 / 22
      0.70       90.7%        2 / 22

There is no good operating point. At full recall it fires on 55% of ordinary
conversation; at usable precision it drops one genuine activation in eleven. The classes
overlap because the transcript has destroyed the distinguishing information: *"hey paul"*
and *"hey pal"* share a phonetic skeleton exactly (`HYPL`), while the true mangling
*"hippow"* scores below the false *"hello"*.

**So ADR-0004 is right, for a reason it did not state.** Its stated premises - per-second
STT billing and audio leaving the machine - were both voided by
[ADR-0015](../Docs/adr/0015-local-gpu-stt.md) making STT local and free. What actually
justifies an audio-level wake-word detector is that the acoustic signal carries what the
transcript throws away, and no amount of text matching recovers it.

Use this as a **confirmation layer behind an audio detector**, or on the text intake path
where a channel message is already addressed to the bot and the wake word is courtesy
rather than a gate. `Activation.score` is exposed so a caller can treat a marginal match
differently from a confident one - the intended use being to answer either way but stay
silent on a decline when confidence was low, so a misfire costs a router call rather than
channel noise.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .knowledge import phonetic, squash

WAKE_WORD = "hey pal"
# The knee of the curve in the docstring, not an optimum - there isn't one. Chosen for the
# confirmation role: behind an audio detector, a miss here is recoverable (the audio gate
# already fired) while a false positive is a paid router call on party chatter.
MIN_SIMILARITY = 0.62
# Below this, treat a match as marginal: worth routing, not worth posting a decline card
# for. Keeps a misfire silent instead of littering the channel.
CONFIDENT = 0.80
# The wake word is two short words, so a mangling rarely spans more. Widening this admits
# utterances that merely happen to contain a similar-sounding token later on.
MAX_LEAD_TOKENS = 3


@dataclass(frozen=True)
class Activation:
    """Whether an utterance was addressed to the assistant, and what remains of it."""
    matched: bool
    query: str          # the utterance with the wake word removed
    heard: str          # the tokens that matched, for diagnosis of near-misses
    score: float

    @property
    def confident(self) -> bool:
        """Confident enough that a decline is worth telling the user about.

        A marginal activation that the router then declines was probably not addressed to
        us at all, and answering "I didn't catch that" to a conversation between two other
        people is worse than saying nothing.
        """
        return self.matched and self.score >= CONFIDENT


def _similar(a: str, b: str) -> float:
    """Similarity over squashed forms, with a phonetic bonus.

    Deliberately the same shape as the lexicon's matcher rather than a new idea: the
    failure being corrected is identical, and two matchers drifting apart would mean the
    wake word and the entity names disagreed about what "close" means.
    """
    from difflib import SequenceMatcher
    sa, sb = squash(a), squash(b)
    if not sa or not sb:
        return 0.0
    score = SequenceMatcher(None, sa, sb).ratio()
    if phonetic(a) == phonetic(b):
        score = max(score, 0.85)
    return score


def detect(utterance: str, wake_word: str = WAKE_WORD,
           threshold: float = MIN_SIMILARITY) -> Activation:
    """Look for the wake word at the head of `utterance`.

    Head-only by design. A wake word matched mid-sentence would fire on someone saying
    "...told him hey pal..." in conversation, and the channel is mostly conversation.
    """
    tokens = re.findall(r"[\w']+", utterance)
    if not tokens:
        return Activation(False, utterance, "", 0.0)

    best = (0.0, 0)
    for n in range(1, min(MAX_LEAD_TOKENS, len(tokens)) + 1):
        lead = " ".join(tokens[:n])
        s = _similar(lead, wake_word)
        if s > best[0]:
            best = (s, n)

    score, n = best
    if score < threshold:
        return Activation(False, utterance, " ".join(tokens[:2]), score)

    # Rebuild the remainder from the original string rather than the tokens, so
    # punctuation and spacing survive into what the router sees.
    consumed = " ".join(tokens[:n])
    idx = utterance.lower().find(tokens[n - 1].lower()) + len(tokens[n - 1])
    return Activation(True, utterance[idx:].lstrip(" ,.:;-").strip(), consumed, score)
