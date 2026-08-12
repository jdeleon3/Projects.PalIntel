"""What the 2026-08-11 play session changed, kept as regression tests.

The first session with capture on produced 41 utterances, 42 clips and **nine human
labels** — the first organic, human-corrected data this project has had. Every test here
encodes something that session proved wrong, and each one is named for the failure rather
than the fix, because the failures are the part worth recognising again.

Two of them reversed decisions that were already written down and already tested. That is
the point of playing.
"""
from __future__ import annotations

import pytest

from palintel import cards, counters
from palintel.activation import overheard
from palintel.execution import get_pal_info
from palintel.knowledge import KnowledgeBase
from palintel.pipeline import Pipeline, build_router
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def pipe(kb: KnowledgeBase) -> Pipeline:
    return Pipeline(kb, build_router(kb, prefer="stub"))


def call(pipe, text):
    return pipe.router.route(text, pipe.kb.lexicon.rank(text), [])


# ------------------------------------------- the two "wrong Pal" labels

@pytest.mark.parametrize("utterance, leader", [
    ("Hey pal, how do I beat Orserk?", "Axel"),
    ("Hey pal, how do I beat Grisbolt?", "Zoe"),
])
def test_a_tower_species_answers_about_the_tower(pipe, utterance, leader):
    """Both of these came back about the FIELD ALPHA and the player pressed "wrong Pal".

    Seven of the nine tower alphas are placed nowhere in the overworld, so the reading
    the name index chose was a fight that cannot be had.
    """
    out = pipe.handle(utterance)
    assert out.card.title == f"How to fight {leader} & {out.call.args['boss'].title()}" \
        or leader in out.card.title
    assert "tower" in out.card.lines[0]


def test_the_games_own_name_for_a_fight_is_fast_pathed(pipe):
    """"How do I beat Axel & Orserk" paid a model round trip: the display name is a
    PAL_NAME_ row, so the lexicon ranks it as a Pal, and it was not in the counterable
    set the fast path checks."""
    c = call(pipe, "Hey pal, how do I beat Axel & Orserk?")
    assert isinstance(c, ToolCall) and c.name == "plan_counters"


# ------------------------------------------- Whisper inventing speech

def test_overheard_media_is_discarded():
    """Not empty, not truncated, and **not invented** - the player's kids were watching
    YouTube and the mic picked it up. Whisper transcribed it accurately, which is why it
    was counted as heard, spent 1.8s and a model call, and was captured as a labelled
    clip, polluting the corpus capture exists to build."""
    assert overheard("Thank you for watching! Please like, subscribe, comment and")


@pytest.mark.parametrize("real", [
    "Hey pal, thanks", "Hey pal, which pals can ranch?",
    "Hey pal, what am I watching for here", "Hey pal, how do I beat Victor?",
])
def test_real_speech_survives_the_overheard_guard(real):
    """The list is closed and small on purpose: discarding a real question is the failure
    this project weighs heaviest, and nobody asks PalIntel to like and subscribe."""
    assert not overheard(real)


# ------------------------------------------- the class that was missing

@pytest.mark.parametrize("utterance, pal", [
    ("Hey pal, what can you tell me about Shroomer?", "Shroomer"),
    ("Hey pal, tell me about Orserk", "Orserk"),
    ("Hey pal, what level is Pin King?", "Penking"),
])
def test_tell_me_about_is_its_own_class(pipe, utterance, pal):
    """Nine of forty-one utterances asked this shape. Seven were answered by the WRONG
    class - a location card for "tell me about Shroomer", a Tier 2 counter plan for "who
    is Victor" - which is worse than declining, because it looks like an answer."""
    c = call(pipe, utterance)
    assert isinstance(c, ToolCall)
    assert (c.name, c.args) == ("get_pal_info", {"pal": pal})


def test_the_info_card_gathers_rather_than_computes(kb: KnowledgeBase):
    card = cards.pal_info_card(get_pal_info(kb, "Lyleen"), kb.job_label)
    assert card.colour == cards.TIER_FACT
    body = card.to_text()
    assert "Grass" in body and "Planting" in body
    # An index, not a replacement: each line points at the card that answers it properly.
    assert 'Ask "where can I find Lyleen"' in body


def test_a_leader_is_not_given_a_pals_stat_line(pipe):
    """"Who is Victor" has no info card: what the datasets hold is the Pal she fights
    with, and answering about a person with a Pal's stat line would repeat the
    wrong-class failure this class exists to remove."""
    assert isinstance(call(pipe, "Hey pal, who is Victor?"), Decline)


@pytest.mark.parametrize("utterance", [
    # The opener "what's X" is NOT an info cue. A first version made it one and it took
    # Q2 from 43 to 42 with a wrong card, and broke three counter prompts.
    "hey pal what's the nearest memorist",
    "hey pal what's the breeding combo for Shaolong",
    "hey pal what's strong against Lyleen",
    "hey pal what's a good partner skill for Digtoise",
])
def test_a_question_opener_is_not_an_intent(pipe, utterance):
    c = call(pipe, utterance)
    assert not (isinstance(c, ToolCall) and c.name == "get_pal_info"), utterance


# ------------------------------------------- cues play added

def test_a_possessive_weakness_is_a_counter_question(pipe):
    """"What's Victor's weakness" - the possessive puts the named entity in target
    position as firmly as "beat X" does. Bare "weakness" stays out."""
    c = call(pipe, "Hey pal, what's Victor's weakness?")
    assert isinstance(c, ToolCall) and c.name == "plan_counters"


def test_the_job_can_trail_the_subject(pipe):
    """"Which pal's can ranch" - both work patterns wanted the job in FRONT, and only the
    -ing form was in the vocabulary."""
    c = call(pipe, "Hey pal, which pal's can ranch?")
    assert isinstance(c, ToolCall)
    assert c.args == {"work": "MonsterFarm"}


@pytest.mark.parametrize("mangled, pal", [
    # A failure RUN: three attempts at one name in ninety seconds, two declined and the
    # third answered with the wrong class.
    ("Hey pal, tell me about Lani", "Lyleen"),
    ("Hey pal tell me about Lening", "Lyleen"),
    ("Hey pal, tell me about Leneen", "Lyleen"),
    ("Hey pal where can I find Celine?", "Selyne"),
])
def test_aliases_harvested_from_unscripted_speech(kb: KnowledgeBase, mangled, pal):
    """The first aliases in this project taken from real play rather than from prompts
    read off a list. Swept against 281 transcripts before adding: worst unrelated match
    0.714, under both floors."""
    assert kb.lexicon.rank(mangled)[0].canonical == pal


# ------------------------------------------- shapes the replay surfaced

@pytest.mark.parametrize("utterance, pal", [
    ("Hey pal, can I ride Azurbe Cryst?", "Azurobe Cryst"),
    ("Hey pal, can I ride Lamball?", "Lamball"),
    ("Hey pal, is Nitewing rideable?", "Nitewing"),
])
def test_can_i_ride_one_named_pal_is_an_info_question(pipe, utterance, pal):
    """A yes/no about ONE Pal. Mount search returns lists and cannot answer it, so this
    routes to the info card rather than becoming a fourth mount shape."""
    c = call(pipe, utterance)
    assert isinstance(c, ToolCall)
    assert (c.name, c.args) == ("get_pal_info", {"pal": pal})


@pytest.mark.parametrize("pal, expected", [
    ("Lamball", "Not rideable"),
    ("Nitewing", "a land mount"),
    ("Jormuntide", "faster in water"),
])
def test_the_mount_line_is_always_present(kb: KnowledgeBase, pal, expected):
    """**Silence is not a no.** The line renders on every info card, including for Pals
    with no saddle, so an omission can never carry the answer - and so the card has one
    behaviour rather than reshaping itself around which question was asked."""
    card = cards.pal_info_card(get_pal_info(kb, pal), kb.job_label)
    assert any(expected in ln for ln in card.lines), card.lines


def test_a_described_subject_still_reaches_mount_search(pipe):
    """"Which swimming mounts are available" describes rather than names, so it must not
    be caught by the single-Pal rideable check."""
    c = call(pipe, "hey pal which swimming mounts are available")
    assert c.name == "find_pals_by_attribute"
    assert c.args == {"mount": True, "medium": "water"}


def test_an_unattached_element_word_defers(pipe):
    """"Which dragons can I ride at level 60" states an element the cue cannot attach -
    the pattern wants "dragon pal" or "dragon type". Answering anyway returned EVERY
    mount at level 60 under a card titled "Mounts": a filter the player stated, silently
    dropped, on the fast path.

    Deferring rather than special-casing `dragons` keeps one rule instead of a rule plus
    an allowlist of one.
    """
    assert isinstance(call(pipe, "Hey pal, which dragons can I ride at level 60?"),
                      Decline)


@pytest.mark.parametrize("utterance, expected", [
    # `ground` and `water` name an element AND a medium. A word the medium cue already
    # consumed is attached, not loose, so these must not defer.
    ("Hey pal, what's the fastest ground mount at level 60",
     {"mount": True, "medium": "land", "player_level": 60}),
    ("hey pal which swimming mounts are available", {"mount": True, "medium": "water"}),
    # And an element that IS attached still works normally.
    ("Hey pal, I need a water pal for watering",
     {"element": "Water", "work": "Watering"}),
])
def test_an_attached_element_word_does_not_defer(pipe, utterance, expected):
    c = call(pipe, utterance)
    assert isinstance(c, ToolCall), utterance
    assert c.args == expected
