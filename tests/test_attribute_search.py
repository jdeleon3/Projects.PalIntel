"""Pal search by attribute — the first class that describes an entity instead of naming one.

Three things are being defended, in descending order of how badly they would fail:

1. **The branch must not steal.** Its guard is the absence of a named entity, so every
   query that names one belongs to another class. STATUS records the near-miss that
   makes this concrete: *"I need a new mining pal"* ranked **Anubis at 0.77**, and Anubis
   is the game's best mining Pal - a location card for it would have read as very nearly
   a correct answer for entirely the wrong reason.
2. **A level band is a set, not a range.** Grizzbolt is placed at (18, 22) and at (80,
   80); "18-80" is arithmetically true, reads as continuous, and would answer "an
   electric Pal at 45" with a species that appears at no such level anywhere.
3. **A widened filter is a different answer.** Feybreak's level bands are lumpy and
   there is genuinely no electric Pal at exactly 60, so the nearest ones come back - and
   the card has to say so, because "the closest thing to" and "an" are different claims.
"""
from __future__ import annotations

import pytest

from palintel import cards
from palintel.execution import find_pals_by_attribute
from palintel.knowledge import KnowledgeBase, PalAttributes
from palintel.routing import StubRouter
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    kb = KnowledgeBase.load("1.0.2")
    if not kb.attributes:
        pytest.skip("work.json not built")
    return kb


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, {n.resource for n in kb.nodes})


def route(router, text):
    return router.route(text, router._lexicon.rank(text), [])


# ------------------------------------------------------------------ level bands

def test_disjoint_bands_are_kept_apart(kb: KnowledgeBase):
    """The Grizzbolt case, which is what the `bands` tuple exists for."""
    g = kb.attributes["Grizzbolt"]
    assert (18, 22) in g.bands
    assert g.level_max == 80
    assert g.spawns_at(20)
    assert g.spawns_at(80)
    # The gap in the middle is real, and a min/max range would have swallowed it.
    assert not g.spawns_at(45)


def test_band_at_returns_the_range_the_player_will_meet():
    a = PalAttributes("X", ("Fire",), {}, None, bands=((10, 12), (60, 64)))
    assert a.band_at(11) == (10, 12)
    assert a.band_at(62) == (60, 64)
    assert a.band_at(30) is None


def test_a_pal_the_overworld_never_places_has_no_band(kb: KnowledgeBase):
    """A raid boss has no wild level. That is a fact, not missing data, and it must not
    be confused with "did not match" - a level filter cannot rule it in or out."""
    bellanoir = kb.attributes["Bellanoir"]
    assert bellanoir.bands == ()
    assert bellanoir.level_min is None
    assert not bellanoir.spawns_at(60)
    # And it is not a one-off: the raid roster and the dungeon-only species are all here.
    assert sum(1 for a in kb.attributes.values() if not a.bands) > 10


# ------------------------------------------------------------------ selection

def test_element_and_level_filter_together(kb: KnowledgeBase):
    r = find_pals_by_attribute(kb, element="Leaf", level=20)
    assert r.level_exact
    assert r.matches
    for m in r.matches:
        assert "Leaf" in m.elements
        assert m.level_gap == 0
        assert m.band[0] <= 20 <= m.band[1]


def test_nothing_at_the_exact_level_widens_and_says_so(kb: KnowledgeBase):
    """Not an edge case: Feybreak places most species at 80, so wild levels are lumpy."""
    r = find_pals_by_attribute(kb, element="Electricity", level=60)
    assert not r.level_exact
    assert r.matches
    assert all(m.level_gap > 0 for m in r.matches)
    # Sorted by how far off they are, nearest first.
    assert [m.level_gap for m in r.matches] == sorted(m.level_gap for m in r.matches)

    card = cards.attribute_card(r)
    assert "Nothing spawns at exactly level 60" in card.lines[0]


def test_exact_matches_are_never_padded_with_near_ones(kb: KnowledgeBase):
    """Mixing them would make the card's own claim true for some rows and false for
    others, with nothing on the card to tell them apart."""
    r = find_pals_by_attribute(kb, element="Leaf", level=20, limit=50)
    assert all(m.level_gap == 0 for m in r.matches)


def test_a_job_sorts_by_that_job_and_only_that_job(kb: KnowledgeBase):
    r = find_pals_by_attribute(kb, work="Mining", limit=8)
    levels = [m.work_level for m in r.matches]
    assert levels == sorted(levels, reverse=True)
    assert all(v > 0 for v in levels)


def test_the_card_prints_the_job_asked_about_not_the_pals_best(kb: KnowledgeBase):
    """Anubis's best job is Handiwork. Asked about mining, the card says Mining."""
    r = find_pals_by_attribute(kb, work="Mining", limit=50)
    anubis = next(m for m in r.matches if m.pal == "Anubis")
    assert anubis.work_level == kb.attributes["Anubis"].work["Mining"]
    card = cards.attribute_card(find_pals_by_attribute(kb, work="Mining"))
    assert "Handiwork" not in "\n".join(card.lines)


def test_job_labels_come_from_the_game_not_the_enum(kb: KnowledgeBase):
    """`EmitFlame` is the pak's spelling; the player reads "Kindling"."""
    card = cards.attribute_card(find_pals_by_attribute(kb, work="EmitFlame"))
    assert "Kindling" in card.title
    assert "EmitFlame" not in card.to_text()


def test_an_impossible_combination_says_what_it_looked_for(kb: KnowledgeBase):
    r = find_pals_by_attribute(kb, element="Normal", work="OilExtraction")
    card = cards.attribute_card(r)
    if not r.matches:
        assert "Looked for" in "\n".join(card.lines)


def test_unplaced_pals_are_reported_rather_than_dropped(kb: KnowledgeBase):
    r = find_pals_by_attribute(kb, element="Dark", level=30)
    assert r.without_a_band > 0
    assert f"{r.without_a_band} more match" in "\n".join(cards.attribute_card(r).lines)


def test_the_card_refuses_to_imply_a_ranking(kb: KnowledgeBase):
    """STATUS's decision carries its own caveat: highest level is a proxy for strongest
    and nothing more."""
    card = cards.attribute_card(find_pals_by_attribute(kb, element="Fire"))
    assert "not a ranking" in card.footer


# ------------------------------------------------------------------ routing

@pytest.mark.parametrize("utterance, expected", [
    # The four questions STATUS records being asked and declined on 2026-08-11, verbatim.
    ("hey pal give me an electric pal that is level 60",
     {"element": "Electricity", "level": 60}),
    ("hey pal what electric pals are around level 60",
     {"element": "Electricity", "level": 60}),
    ("hey pal I need a new mining pal", {"work": "Mining"}),
    ("hey pal what pal is best at mining", {"work": "Mining"}),
    # And the shapes that fall out of the same vocabulary.
    ("hey pal show me a dragon pal", {"element": "Dragon"}),
    ("hey pal I need a water pal for watering",
     {"element": "Water", "work": "Watering"}),
    ("hey pal what pal is best for kindling", {"work": "EmitFlame"}),
])
def test_the_branch_answers_the_questions_it_was_built_for(router, utterance, expected):
    call = route(router, utterance)
    assert isinstance(call, ToolCall), f"declined: {getattr(call, 'reason', '')}"
    assert call.name == "find_pals_by_attribute"
    assert call.args == expected


@pytest.mark.parametrize("utterance", [
    # Every one of these NAMES something, which is the guard.
    "hey pal where's the nearest coal",
    "hey pal where can I find Chillet",
    "hey pal what does Vanwyrm drop",
    "hey pal how do I beat Anubis",
    # No entity either, but no attribute cue - a location question with a job word in it.
    # The wake address must not supply the "pal" that would make this look like a roster
    # query.
    "hey pal where can I go mining",
    # An element word with no type noun. "fire" appears in questions about weapons,
    # cooking and kindling far more often than about typing.
    "hey pal do I need fire resistance",
])
def test_the_branch_abstains_wherever_something_is_named(router, utterance):
    call = route(router, utterance)
    assert not (isinstance(call, ToolCall)
                and call.name == "find_pals_by_attribute"), f"claimed {utterance!r}"


def test_a_level_alone_is_not_this_class(router):
    """"Any pals at level 60" is ninety Pals, which is an index rather than an answer."""
    call = route(router, "hey pal any pals at level 60")
    assert not (isinstance(call, ToolCall)
                and call.name == "find_pals_by_attribute")


def test_the_anubis_near_miss_stays_a_search(router):
    """0.77 on "a new", below the 0.85 Pal floor. If that floor ever moves, this test is
    the one that notices - the query would silently become a Anubis location card."""
    candidates = router._lexicon.rank("hey pal I need a new mining pal")
    top = candidates[0]
    assert top.canonical == "Anubis" and top.score < 0.85
    call = route(router, "hey pal I need a new mining pal")
    assert isinstance(call, ToolCall) and call.name == "find_pals_by_attribute"


# ------------------------------------------------------------------ the model path

def test_the_unified_schema_unpacks_a_search():
    from palintel.routing_unified import unpack

    name, args = unpack("answer_query", {
        "query_class": "pal_search", "pals": [], "resources": [], "items_named": [],
        "target": None, "max_player_level": None,
        "pal_elements": ["Electricity"], "pal_work": [], "pal_level": 60})
    assert name == "find_pals_by_attribute"
    assert args == {"element": "Electricity", "level": 60}


def test_pal_level_is_never_read_as_the_players_level():
    """The two live in different slots on purpose. STATUS's 2026-08-11 decision is that
    "level" in this class always means the PAL's, and `max_player_level` gates resource
    nodes by what the player can survive - one word, two meanings, two fields."""
    from palintel.routing_unified import unpack

    _, args = unpack("answer_query", {
        "query_class": "pal_search", "pals": [], "resources": [], "items_named": [],
        "target": None, "max_player_level": 12,
        "pal_elements": ["Fire"], "pal_work": [], "pal_level": 60})
    assert args == {"element": "Fire", "level": 60}
