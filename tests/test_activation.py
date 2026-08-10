"""Wake-word activation.

These pin behaviour that was measured, not behaviour that was hoped for. The headline
finding is negative: text-level wake-word matching cannot separate genuine activations
from party chatter, because STT destroys the distinguishing signal. The tests record the
shape of that limit so a future change is measured against it rather than against
intuition.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from palintel.activation import CONFIDENT, WAKE_WORD, detect

REPO = Path(__file__).resolve().parents[1]
RESULTS = REPO / "data" / "stt_eval" / "quiet" / "results.json"


@pytest.fixture(scope="module")
def heard() -> list[str]:
    if not RESULTS.exists():
        pytest.skip("no recorded transcripts")
    return [r["boosted_text"] for r in json.loads(RESULTS.read_text(encoding="utf-8"))
            if r["group"] == "utterance"]


def test_clean_wake_word_activates_and_is_stripped():
    a = detect("hey pal where's the nearest coal")
    assert a.matched and a.confident
    assert a.query == "where's the nearest coal"


def test_punctuation_does_not_leak_into_the_query():
    """The router sees `query`, so a leading comma would reach the model."""
    assert detect("Hey pal, what element is Chikipi?").query == "what element is Chikipi?"


@pytest.mark.parametrize("utterance", [
    "Hippel is Elmora Lux better than the normal one",
    "Apel, how do I breed Snok?",
    "Hippow compare Virdach and Amone for combat.",
])
def test_mangled_wake_words_are_recognised_as_marginal_at_best(utterance: str):
    """These are real transcripts of someone saying "hey pal".

    They are not required to match - the measurement says they mostly cannot - but they
    must never come back *confident*, because confidence is what decides whether a
    decline gets posted to the channel.
    """
    assert not detect(utterance).confident


def test_recall_on_real_transcripts_has_not_regressed(heard: list[str]):
    """92.8% at the default threshold, measured over 236 recorded utterances.

    A floor rather than a target: the honest fix is an audio-level detector, and this
    guards against a change that quietly makes the text layer worse in the meantime.
    """
    hits = sum(1 for h in heard if detect(h).matched)
    assert hits / len(heard) >= 0.90


def test_conversation_that_must_not_be_answered():
    """Not all of these pass, and that is the documented limitation.

    "hey paul" is phonetically identical to "hey pal" and is unfixable at this layer.
    What is required is narrower: chatter with no "hey" at all must never fire.
    """
    for line in ["pass me the healing potion", "no way that thing hit hard",
                 "I am out of ammo", "the servers lagging again",
                 "that boss hits so hard"]:
        assert not detect(line).matched, line


def test_a_wake_word_mid_sentence_does_not_fire():
    """The channel is mostly conversation; matching anywhere would fire constantly."""
    assert not detect("so I told him hey pal calm down").matched


def test_empty_and_whitespace_are_safe():
    for s in ["", "   ", "\n"]:
        assert not detect(s).matched


def test_threshold_is_configurable_per_caller():
    """The audio-gated path wants recall; the text path wants precision."""
    loose = detect("Apel, how do I breed Snok?", threshold=0.30)
    strict = detect("Apel, how do I breed Snok?", threshold=0.90)
    assert loose.matched and not strict.matched
    assert loose.score == strict.score  # scoring is independent of the cutoff


def test_confident_requires_matched():
    assert not detect("no way that thing hit hard").confident


def test_custom_wake_word():
    a = detect("computer where is the coal", wake_word="computer")
    assert a.matched and a.query == "where is the coal"
    assert not detect("computer where is the coal", wake_word=WAKE_WORD).matched
