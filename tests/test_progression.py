"""Q6 progression — the candidate set, the derived level floor, and the Tier 2 guard.

Three things here are worth more than the rest, and they are the three that can put a
confidently wrong card in front of a player:

* **The level floor is a floor.** It is inferred from the unlocked set, so it must only
  ever hide something available and never offer something that is not.
* **The two point pools never mix.** Adding them would tell a player they can afford
  something they cannot.
* **`validate` drops anything the computation did not produce**, exactly as
  `counters.validate` does.

Plus one wiring test, because the counter fast path shipped dark for a day by being
passed to one `StubRouter` and not the other.
"""
from __future__ import annotations

import pytest

from palintel import cards
from palintel.progression import (Blocker, Candidate, PlayerTech, ProgressionError,
                                  Technology, categories, load, plan, validate)


def tech(tech_id, *, level=10, cost=1, currency="technology", prereq=(),
         tower=None, research=None, category="BuildObject", name=None) -> Technology:
    return Technology(tech_id=tech_id, name=name or tech_id, required_level=level,
                      cost=cost, currency=currency, prerequisites=tuple(prereq),
                      requires_tower=tower, requires_research=research,
                      category=category, unlocks=())


@pytest.fixture
def table(monkeypatch):
    """A small table in place of the 588-row dataset, so these tests state their own
    inputs rather than depending on a build."""
    rows = {
        "Workbench": tech("Workbench", level=1),
        "Furnace": tech("Furnace", level=10, cost=2),
        "BreedFarm": tech("BreedFarm", level=19, cost=2, currency="ancient",
                          tower="ForestBoss"),
        "Grapple2": tech("Grapple2", level=17, cost=2, currency="ancient",
                         prereq=("Grapple1",)),
        "Grapple1": tech("Grapple1", level=12, cost=1, currency="ancient"),
        "Refinery": tech("Refinery", level=40, cost=5, research="Mining5"),
        "Spear": tech("Spear", level=15, cost=3, category="Weapon"),
    }
    monkeypatch.setattr("palintel.progression.load", lambda version="1.0.2": rows)
    return rows


# ------------------------------------------------------------------ the level floor

def test_the_level_floor_is_the_highest_thing_already_unlocked(table):
    state = PlayerTech(unlocked=frozenset({"Workbench", "Furnace"}))
    assert state.level_floor(table) == 10


def test_an_unread_save_has_no_floor_rather_than_a_floor_of_zero(table):
    """Zero would filter nothing and read as a real reading. None means the gate did not
    run, which is what the card has to say."""
    assert PlayerTech().level_floor(table) is None


def test_the_floor_only_hides_and_never_offers(table):
    """The whole safety argument for inferring a level at all.

    A player whose floor is 10 but who is really level 20 sees Spear (15) reported as
    blocked. That is wrong and it is wrong in the safe direction: they are told to wait
    for something they could already have, never told to buy something they cannot.
    """
    state = PlayerTech(unlocked=frozenset({"Workbench", "Furnace"}))
    result = plan(state, limit=10)
    assert result.level == 10 and result.level_is_a_floor
    spear = next(c for c in result.candidates if c.tech.tech_id == "Spear")
    assert spear.blocked_by is Blocker.LEVEL


def test_a_stated_level_beats_the_inferred_floor(table):
    """A reading wins over an inference. The player said 20; the floor said 10."""
    state = PlayerTech(unlocked=frozenset({"Workbench", "Furnace"}))
    result = plan(state, player_level=20, limit=10)
    assert result.level == 20 and not result.level_is_a_floor
    spear = next(c for c in result.candidates if c.tech.tech_id == "Spear")
    assert spear.researchable


# ------------------------------------------------------------------ two currencies

def test_the_pools_are_never_added_together(table):
    """40 ordinary points do not buy a 2-point ancient technology."""
    state = PlayerTech(unlocked=frozenset({"Workbench", "Furnace", "Grapple1"}),
                       points=40, ancient_points=0,
                       towers_defeated=frozenset({"ForestBoss"}))
    result = plan(state, player_level=80, limit=10)
    farm = next(c for c in result.candidates if c.tech.tech_id == "BreedFarm")
    assert farm.blocked_by is Blocker.POINTS


def test_the_currency_filter_narrows_to_one_pool(table):
    state = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99,
                       towers_defeated=frozenset({"ForestBoss"}))
    result = plan(state, currency="ancient", player_level=80, limit=10)
    assert {c.tech.tech_id for c in result.candidates} == {"BreedFarm", "Grapple1",
                                                           "Grapple2"}
    assert result.currency == "ancient"


def test_unread_points_are_optimistic_rather_than_blocking(table):
    """A pool nobody read must not hide a valid suggestion behind a number nobody has."""
    state = PlayerTech(unlocked=frozenset({"Workbench"}))
    result = plan(state, player_level=80, limit=10)
    assert all(c.blocked_by is not Blocker.POINTS for c in result.candidates)


# ------------------------------------------------------------------ the other gates

def test_a_prerequisite_that_is_not_unlocked_blocks(table):
    state = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99)
    result = plan(state, player_level=80, limit=10)
    assert next(c for c in result.candidates
                if c.tech.tech_id == "Grapple2").blocked_by is Blocker.PREREQUISITE


def test_an_unbeaten_tower_blocks_only_when_the_flags_were_read(table):
    """With no flags at all, every tower-gated technology would report as blocked - a
    claim about a set nobody inspected, which is the failure the counter card separates
    `roster_known` to avoid."""
    read = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99,
                      towers_defeated=frozenset())
    assert next(c for c in plan(read, player_level=80, limit=10).candidates
                if c.tech.tech_id == "BreedFarm").blocked_by is Blocker.TOWER

    unread = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99)
    assert next(c for c in plan(unread, player_level=80, limit=10).candidates
                if c.tech.tech_id == "BreedFarm").researchable


def test_lab_research_is_reported_and_never_filtered_on(table):
    """The save cannot be checked for it. Excluding claims "you cannot", including
    silently claims "you can"; naming it is the only true option."""
    state = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99)
    result = plan(state, player_level=80, limit=10)
    refinery = next(c for c in result.candidates if c.tech.tech_id == "Refinery")
    assert refinery.researchable
    assert result.research_gated == 1
    assert "Mining5" in cards.progression_card(result).to_text()


def test_an_unlocked_technology_is_not_a_candidate(table):
    state = PlayerTech(unlocked=frozenset({"Workbench", "Furnace"}))
    result = plan(state, limit=10)
    assert "Workbench" not in {c.tech.tech_id for c in result.candidates}


def test_a_goal_filters_to_the_games_own_category(table):
    state = PlayerTech(unlocked=frozenset({"Workbench"}), points=99)
    result = plan(state, goal="Weapon", player_level=80, limit=10)
    assert {c.tech.tech_id for c in result.candidates} == {"Spear"}


# ------------------------------------------------------------------ the Tier 2 guard

def test_validate_drops_a_technology_the_computation_did_not_produce():
    """The whole Tier 2 discipline. A model may order and phrase; it may not add."""
    got = [Candidate(tech("Furnace"), Blocker.NONE)]
    kept, discarded = validate(["Furnace", "Nuclear Reactor"], got)
    assert [c.tech.tech_id for c in kept] == ["Furnace"]
    assert discarded == ["Nuclear Reactor"]


def test_validate_matches_a_display_name_case_insensitively():
    got = [Candidate(tech("BreedFarm", name="Breeding Farm"), Blocker.NONE)]
    kept, _ = validate(["breeding farm"], got)
    assert [c.tech.tech_id for c in kept] == ["BreedFarm"]


def test_validate_does_not_repeat_one_technology_named_twice():
    got = [Candidate(tech("BreedFarm", name="Breeding Farm"), Blocker.NONE)]
    kept, _ = validate(["BreedFarm", "Breeding Farm"], got)
    assert len(kept) == 1


# ------------------------------------------------------------------ the card

def test_an_unread_save_says_so_instead_of_recommending_anything(table):
    """"Nothing to research" and "I have not looked" are different answers."""
    card = cards.progression_card(plan(PlayerTech()))
    assert "haven't read your save" in card.title
    assert card.colour == cards.TIER_DECLINE


def test_the_card_is_amber_because_the_selection_is_a_recommendation(table):
    state = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99)
    assert cards.progression_card(plan(state)).colour == cards.TIER_ADVICE


def test_the_card_names_the_currency_on_every_line(table):
    """A cost with no currency beside it is a number out of the wrong balance."""
    state = PlayerTech(unlocked=frozenset({"Workbench", "Grapple1"}), points=99,
                       ancient_points=99, towers_defeated=frozenset({"ForestBoss"}))
    text = cards.progression_card(plan(state, player_level=80, limit=10)).to_text()
    assert "2 ancient pt" in text and "2 pt" in text


def test_the_card_says_the_level_was_inferred(table):
    state = PlayerTech(unlocked=frozenset({"Workbench", "Furnace"}))
    text = cards.progression_card(plan(state)).to_text()
    assert "at least level 10" in text


def test_the_card_says_when_no_level_was_known_at_all(table):
    """A list that looks filtered and is not is worse than one that says so."""
    text = cards.progression_card(plan(PlayerTech(unlocked=frozenset()))).to_text()
    assert "no level known" in text


def test_the_card_reports_what_it_did_not_show(table):
    """Five rows out of a hundred must not read as ninety-five rejected on merit."""
    state = PlayerTech(unlocked=frozenset({"Workbench"}), points=99, ancient_points=99,
                       towers_defeated=frozenset())
    text = cards.progression_card(plan(state, player_level=15, limit=2)).to_text()
    assert "still locked" in text and "need a higher level" in text


# ------------------------------------------------------------------ the real dataset

def test_the_built_table_loads_and_its_categories_match_the_schema_enum():
    """The schema's enum is written out because a schema is a contract, so a patch that
    adds a category must fail here rather than silently offering the model a goal the
    data no longer serves."""
    from palintel.routing_unified import TECH_CATEGORIES

    try:
        rows = load("1.0.2")
    except ProgressionError:
        pytest.skip("tech.json not built")
    assert len(rows) > 500
    assert tuple(categories("1.0.2")) == TECH_CATEGORIES


def test_no_built_name_is_raw_markup():
    """**26 rows published markup as their name and the first build shipped it.**

    The tech name table stores pointers - `<mapObjectName id=|BreedFarm|/>` - and some
    rows spell the tag `mapObjectname`. A case-sensitive pattern let those through as
    "already plain text", so the Large Incubator's name was the literal string
    `<mapObjectname id=|MultiHatchingPalEgg|/>`. Well-formed, entirely wrong, and found
    only because a card was read rather than a count checked.
    """
    try:
        rows = load("1.0.2")
    except ProgressionError:
        pytest.skip("tech.json not built")
    leaked = [t.tech_id for t in rows.values() if "<" in t.name or "|" in t.name]
    assert not leaked, f"markup published as a name: {leaked[:5]}"


def test_every_prerequisite_in_the_built_table_exists():
    """A prerequisite naming a row that does not exist makes `prereqs <= unlocked`
    permanently false: a technology that can never be recommended, with nothing on any
    card to say why."""
    try:
        rows = load("1.0.2")
    except ProgressionError:
        pytest.skip("tech.json not built")
    for t in rows.values():
        for p in t.prerequisites:
            assert p in rows, f"{t.tech_id} requires {p}, which is not a row"
