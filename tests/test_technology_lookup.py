"""Looking up ONE named technology, without putting 588 names anywhere global.

The design question this class turns on: 46 technologies have single-word names and
twelve are ordinary English — `Mine`, `Ranch`, `Mill`, `Sword`, `Sign`. In the lexicon
they would rank against every utterance, which is precisely why 151 item names are kept
out of it. Scoped to the object of an unlock verb they are safe, and it costs no schema
tokens at all — unlike `item_source`, which pays a 151-value enum on every request for
the same capability.

So the tests come in two halves: the matcher resolving ordinary words correctly, and the
branch refusing to run anywhere it would be dangerous.
"""
from __future__ import annotations

import pytest

from palintel import cards, progression
from palintel.knowledge import KnowledgeBase
from palintel.routing import StubRouter
from palintel.tools import ToolCall


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}),
                      cues="wide", progression=True)


def call(router, text):
    c = router.route(text, router._lexicon.rank(text), [])
    return c if isinstance(c, ToolCall) else None


def has_tech() -> bool:
    try:
        progression.load("1.0.2")
        return True
    except progression.ProgressionError:
        return False


# ------------------------------------------------------------------ the matcher

@pytest.mark.parametrize("query,expected", [
    ("breeding farm", "BreedFarm"),
    ("the breeding farm", "BreedFarm"),
    ("egg incubator", "Special_HatchingPalEgg"),
    ("large incubator", "MultiHatchingPalEgg"),
    ("ore mining site", "Product_CopperPit"),
])
def test_a_named_technology_resolves(query, expected):
    if not has_tech():
        pytest.skip("tech.json not built")
    found = progression.find(query)
    assert found and found[0].tech_id == expected


def test_ordinary_single_words_resolve_here_and_belong_nowhere_global():
    """`Mine`, `Ranch` and `Mill` are real technology names AND ordinary English. They
    work because this matcher only ever sees the object of an unlock verb - "where can I
    go mining" never reaches it."""
    if not has_tech():
        pytest.skip("tech.json not built")
    for query, name in (("a mine", "Mine"), ("ranch", "Ranch"), ("the mill", "Mill")):
        found = progression.find(query)
        assert found and found[0].name == name


def test_a_technology_whose_name_never_resolved_is_still_findable():
    """Twenty-five names resolve to nothing and carry their tech id instead. That is a
    build defect for display, but it does not have to be one for lookup: the id is
    usually the English word anyway, and both are matched."""
    if not has_tech():
        pytest.skip("tech.json not built")
    tech = progression.load()["Musket"]
    assert tech.name == tech.tech_id                        # the fallback, not a name
    found = progression.find("a musket")
    assert found and found[0].tech_id == "Musket"


def test_something_that_is_not_a_technology_matches_nothing():
    if not has_tech():
        pytest.skip("tech.json not built")
    assert progression.find("a nuclear reactor") is None
    assert progression.find("anubis") is None


@pytest.mark.parametrize("query", [
    "cake",             # several
    "grappling gun",    # five tiers - and `squash` strips the digits that separate them
])
def test_two_plausible_readings_defer_rather_than_pick_one(query):
    """The rule ROUTING_POLICY states for entities, applied here for the same reason: the
    answer is a card and a card cannot ask which one you meant.

    `grappling gun` is the sharp case. `squash` deletes digits on purpose - ASR splits an
    invented word into several English ones and the digits are noise - so all five tiers
    of `GrapplingGun`..`GrapplingGun5` squash to the same string and tie at 1.00. A
    confident answer here would be a coin flip between tiers, which is exactly the
    well-formed-and-wrong failure this project refuses."""
    if not has_tech():
        pytest.skip("tech.json not built")
    assert progression.find(query) is None


# ------------------------------------------------------------------ requirements

def test_every_gate_is_listed_not_only_the_first_missing_one():
    """`_blocker` collapses to the most fundamental failure because a ranked list needs
    one reason. "What do I still need" is a different question, and naming only the first
    would send somebody to beat a tower without mentioning they are nine levels short."""
    if not has_tech():
        pytest.skip("tech.json not built")
    tech = progression.load()["BreedFarm"]
    state = progression.PlayerTech(unlocked=frozenset({"Workbench"}), points=0,
                                   ancient_points=0, towers_defeated=frozenset())
    reqs = progression.requirements(tech, state)
    names = " ".join(r.name for r in reqs)
    assert "level 19" in names and "ancient" in names and "ForestBoss" in names
    assert sum(1 for r in reqs if r.met is False) >= 2      # more than one gate named


def test_lab_research_is_reported_as_uncheckable_rather_than_failed():
    if not has_tech():
        pytest.skip("tech.json not built")
    gated = next(t for t in progression.load().values() if t.requires_research)
    reqs = progression.requirements(gated, progression.PlayerTech(
        unlocked=frozenset(), points=99, ancient_points=99))
    lab = next(r for r in reqs if "lab research" in r.name)
    assert lab.met is None


def test_an_unread_save_makes_gates_unknown_not_unmet():
    """"I have not looked" and "you do not have it" are different answers all the way to
    the card."""
    if not has_tech():
        pytest.skip("tech.json not built")
    reqs = progression.requirements(progression.load()["BreedFarm"],
                                    progression.PlayerTech())
    assert all(r.met is not False for r in reqs)
    assert any(r.met is None for r in reqs)


# ------------------------------------------------------------------ the card

def test_an_unlocked_technology_says_so_instead_of_listing_gates():
    if not has_tech():
        pytest.skip("tech.json not built")
    tech = progression.load()["Workbench"]
    state = progression.PlayerTech(unlocked=frozenset({"Workbench"}))
    card = cards.technology_card(tech, progression.requirements(tech, state), True, 1.0)
    assert "already have" in card.title and card.colour == cards.TIER_FACT


def test_a_reachable_technology_says_you_can_research_it_now():
    if not has_tech():
        pytest.skip("tech.json not built")
    tech = progression.load()["BreedFarm"]
    # The level floor is inferred from what is unlocked, so clearing BreedFarm's level 19
    # means holding something that also costs 19 - `Cement` does.
    state = progression.PlayerTech(unlocked=frozenset({"Cement"}), points=99,
                                   ancient_points=99,
                                   towers_defeated=frozenset({"ForestBoss"}))
    card = cards.technology_card(tech, progression.requirements(tech, state), False, 1.0)
    assert card.colour == cards.TIER_ADVICE
    assert "research this now" in card.to_text()


def test_an_unreachable_technology_counts_what_is_in_the_way():
    if not has_tech():
        pytest.skip("tech.json not built")
    tech = progression.load()["BreedFarm"]
    state = progression.PlayerTech(unlocked=frozenset({"Workbench"}), points=0,
                                   ancient_points=0, towers_defeated=frozenset())
    text = cards.technology_card(tech, progression.requirements(tech, state),
                                 False, 1.0).to_text()
    assert "Not yet" in text and "still in the way" in text


# ------------------------------------------------------------------ the branch

@pytest.mark.parametrize("text,tech_id", [
    ("how do I unlock the breeding farm", "BreedFarm"),
    ("how do I get the egg incubator", "Special_HatchingPalEgg"),
    ("what do I need for the breeding farm", "BreedFarm"),
    ("how can I unlock the ore mining site", "Product_CopperPit"),
])
def test_the_branch_claims_a_named_technology(router, text, tech_id):
    if not has_tech():
        pytest.skip("tech.json not built")
    c = call(router, text)
    assert c and c.name == "find_technology" and c.args["tech_id"] == tech_id


def test_where_do_i_get_is_not_an_unlock_question(router):
    """**The sweep found this one.** With the verb alone the branch claimed "where do I
    get high quality pal oil" - an item-source question answered with a technology.
    "Where" asks for a place or a source; "how" asks what it takes."""
    assert (call(router, "where do I get high quality pal oil") or
            ToolCall("x")).name != "find_technology"


def test_a_named_pal_is_not_a_technology(router):
    """"How do I unlock Anubis" is about catching one. Checked before the name matcher
    runs, so a species can never be matched against a technology."""
    assert (call(router, "how do I unlock Anubis") or ToolCall("x")).name \
        != "find_technology"


def test_it_does_not_collide_with_what_should_i_research_next(router):
    """Two technology questions, two classes: one names a technology and one asks for a
    recommendation. Neither pattern matches the other's phrasing."""
    if not has_tech():
        pytest.skip("tech.json not built")
    assert call(router, "what should I research next").name == "suggest_next_unlock"
    assert call(router, "how do I unlock the breeding farm").name == "find_technology"


def test_the_branch_is_off_without_the_dataset(kb):
    off = StubRouter(kb.lexicon, sorted({n.resource for n in kb.nodes}), cues="wide")
    assert (call(off, "how do I unlock the breeding farm") or ToolCall("x")).name \
        != "find_technology"
