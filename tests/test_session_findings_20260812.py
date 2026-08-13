"""What the 2026-08-12 play session changed, kept as regression tests.

The sibling of `test_session_findings.py`, which holds the 2026-08-11 session. This one
covers the first play of Phase 4: 52 utterances, six human labels, and three defects that
only travelling to a coordinate could have found.

The common shape across all three, and the reason they are filed together: **a fact the
system was already holding and not printing.** The alpha Anubis was in the dataset and
ranked out of the answer; the nearest Lovander area was in the dataset and ranked out of
the answer; the spoken coordinate was in the transcript and could not be read. None of
them is a missing-data problem, and none would have been caught by a scorer - the cards
were well-formed, and two of them were internally consistent.
"""
from __future__ import annotations

import pytest

from palintel.cards import spawn_card
from palintel.execution import find_pal_spawns
from palintel.knowledge import KnowledgeBase
from palintel.pipeline import Pipeline, PlayerState, build_router
from palintel.routing import coordinates
from palintel.tools import Decline, ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def pipe(kb: KnowledgeBase) -> Pipeline:
    return Pipeline(kb, build_router(kb, prefer="stub"))


# Where the save had the player standing during the session.
STOOD_AT = (284.0, 625.0)


# ------------------------------------------- the card that sent the player somewhere lethal

def test_a_field_alpha_is_named_when_ordinary_spawns_are_the_answer(kb: KnowledgeBase):
    """Asked "where can I find Anubis", the card offered level 68-72 areas 2,000 units
    away. The player could not survive there - and the level 55 field alpha they had
    already beaten sits 831 units away, in the same dataset, with the highest density of
    all 26 Anubis areas. `SPAWN_KINDS` falls through to the first kind with any rows, so
    25 ordinary areas hid it completely.
    """
    result = find_pal_spawns(kb, "Anubis", near=STOOD_AT)
    assert result.kind == "normal", "ordinary spawns exist, so they are still the answer"
    assert result.field_alpha is not None
    assert result.field_alpha.kind == "alpha"
    assert result.field_alpha.level_min == 55
    # Nearer than everything that ranked, which is the whole complaint.
    assert (result.field_alpha.distance_to(*STOOD_AT)
            < min(a.distance_to(*STOOD_AT) for a in result.areas))
    assert "Field alpha:" in "\n".join(spawn_card(result).lines)


def test_the_alpha_row_is_not_repeated_when_the_alpha_is_the_answer(kb: KnowledgeBase):
    """`kind_substituted` already leads the card with "the only one out there is an
    alpha". Saying it twice, in two formats, reads as two different findings."""
    result = find_pal_spawns(kb, "Anubis", kind="alpha", near=STOOD_AT)
    assert result.areas and result.areas[0].kind == "alpha"
    assert result.field_alpha is None


# ------------------------------------------- three markers in one place

def test_a_materially_nearer_area_is_named(kb: KnowledgeBase):
    """Asked for Lovander, the card gave three areas 818-872 units away - which read as
    one place, because density is spatially clustered and the top three by density are
    all inside one habitat. The player was STANDING in a Lovander area at the time.
    """
    result = find_pal_spawns(kb, "Lovander", near=STOOD_AT)
    assert result.nearest is not None
    assert result.nearest.distance_to(*STOOD_AT) < 20
    assert result.nearest not in result.areas
    card = "\n".join(spawn_card(result).lines)
    assert "Nearest:" in card
    # The ranking is unchanged and the card must say so, or the two rows read as a
    # contradiction rather than as answers to two different questions.
    assert "not by distance" in spawn_card(result).footer


def test_a_marginally_nearer_area_is_not_named(kb: KnowledgeBase):
    """The bar exists so the row means something. Anubis's closest ordinary area is 1,962
    units out against 1,997 for the ranked one - 2% nearer, lower share, and a row that
    would change nothing."""
    result = find_pal_spawns(kb, "Anubis", near=STOOD_AT)
    assert result.nearest is None


def test_no_position_means_no_nearest_row(kb: KnowledgeBase):
    result = find_pal_spawns(kb, "Lovander")
    assert result.nearest is None
    assert "sorted by likelihood" in spawn_card(result).footer


# ------------------------------------------- the minus sign nobody can pronounce

@pytest.mark.parametrize("utterance, expected", [
    # Verbatim from the session log. Both came back rating the player's own position.
    ("rate the spot at 9999, negative 9999", (9999.0, -9999.0)),
    ("rate the spot at 185, negative 475", (185.0, -475.0)),
    ("rate the spot at 185 minus 475", (185.0, -475.0)),
    ("rate the spot at negative 185, negative 475", (-185.0, -475.0)),
    # Written forms, which always worked and must keep working.
    ("rate the spot at (185, -475)", (185.0, -475.0)),
    ("rate this spot at 185 -475", (185.0, -475.0)),
])
def test_a_spoken_minus_is_a_minus(utterance, expected):
    assert coordinates(utterance) == expected


@pytest.mark.parametrize("utterance", [
    # The sentences `_COORD_FORMS` is strict FOR. A spoken minus must not loosen them.
    "rate this base at level 20, 30 stone",
    "how many pals, 3 pals and 20 stone",
    "does this have a negative effect at level 20",
])
def test_the_strictness_that_survived_the_spoken_minus(utterance):
    assert coordinates(utterance) is None


def test_an_unreadable_coordinate_asks_rather_than_substituting(pipe):
    """"rate the base location at 321-500" rated (284, 625) under the title "Where you're
    standing" - a confident card about somewhere the player did not name.

    Deferring rather than widening the parser is the deliberate half: reading `321-500` as
    (321, -500) would read "level 30-40" as (30, -40), in bounds and confidently wrong.
    """
    text = "Hey pal, rate the base location at 321-500"
    call = pipe.router.route(text, pipe.kb.lexicon.rank(text), [])
    assert isinstance(call, Decline) and call.needs_restatement
    assert "coordinate" in call.reason


def test_an_off_map_coordinate_can_be_refused_by_voice(pipe, kb: KnowledgeBase):
    """The off-map refusal needs a coordinate to refuse, so while no spoken negative
    parsed it was unreachable from the only channel it was ever used on."""
    text = "Hey pal, rate the spot at 9999, negative 9999"
    out = pipe.handle(text, PlayerState(player_coords=STOOD_AT))
    assert isinstance(out.call, ToolCall) and out.call.name == "rate_base_site"
    assert "off my map" in out.card.title.lower()


def test_a_bare_rating_still_uses_where_you_stand(pipe):
    """The fall-through is only wrong when a place WAS named. "rate this spot" names none,
    and the player's position is exactly the right answer."""
    text = "Hey pal, rate this spot."
    call = pipe.router.route(text, pipe.kb.lexicon.rank(text), [])
    assert isinstance(call, ToolCall) and call.name == "rate_base_site"
    assert "coordinate" not in call.args


# ------------------------------------------- "rate my base", from inside a different one

def test_rate_my_base_starts_with_the_one_you_are_standing_in(kb: KnowledgeBase):
    """`MAX_CARDS` is 2 and the order was the save's, so a player standing 0.5 units inside
    base 3 was shown bases 1 and 2 - both over a thousand units away - and told "1 more
    base not shown". The card had the player's position and every base's position on it.
    """
    camps = [(229.0, -487.0), (72.0, -399.0), (284.0, 625.0)]
    state = PlayerState(player_coords=(284.0, 625.0), base_camps=camps)
    out = Pipeline(kb, build_router(kb, prefer="stub")).handle("rate my base", state)
    assert out.cards[0].lines[0] == "**(284, 625)**"
    assert out.cards[0].title.startswith("Your base 1")


def test_without_a_position_the_saves_order_is_kept(kb: KnowledgeBase):
    """Nothing to sort by is not a reason to invent one."""
    camps = [(229.0, -487.0), (72.0, -399.0), (284.0, 625.0)]
    out = Pipeline(kb, build_router(kb, prefer="stub")).handle(
        "rate my base", PlayerState(base_camps=camps))
    assert out.cards[0].lines[0] == "**(229, -487)**"


# ------------------------------------------- the resource the card said did not exist

def test_crude_oil_is_placed_after_all(kb: KnowledgeBase):
    """A card asserted "crude oil isn't a mineable node - it comes from oil rigs, so there
    are no map locations to give you". A player who had stood on one said otherwise.

    185 `BP_LevelObject_OilField_C` actors are placed across the map; the blueprint's CDO
    says `ProvidableStaticItemId: CrudeOil`; and the game's own item text reads "Obtained
    by installing a Crude Oil Extractor in an oil field." The extraction that "found no
    spawner class" was filtering on `BP_PalMapObjectSpawner*`.

    **The fourth time in this project that a filter written for one purpose was read as a
    census of the world**, and the first where the mistaken conclusion was published as a
    sentence on a card rather than left in a dataset.
    """
    oil = [n for n in kb.nodes if n.resource == "crude_oil"]
    assert oil, "185 oil fields are placed; a card said there were none"
    assert "crude_oil" in kb.provided_resources
    # Spread over the island, not clustered at the oil rigs.
    assert max(n.map_x for n in oil) - min(n.map_x for n in oil) > 1000


def test_the_unplaced_mechanism_is_kept_and_empty(kb: KnowledgeBase):
    """`NOT_PLACED` was right to exist and wrong about its one member. Deleting it would
    lose the guard that stops "no search results" reading as "no such thing in the world";
    keeping an entry that is false would keep publishing the false one."""
    from palintel.cards import NOT_PLACED
    assert NOT_PLACED == {}
    assert set(kb.lexicon.resources()) == {n.resource for n in kb.nodes}
