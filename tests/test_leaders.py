"""The tower leader mapping: "how do I beat Victor".

**Two things are on trial here and only one of them is the feature.** The first is that
Victor resolves at all. The second, and the one that would produce a confidently wrong
card if it broke, is that Victor resolves to the *tower* and not to the field alpha of
the same species - `GYM_BlackGriffon` and `BOSS_BlackGriffon` are both called Shadowbeak,
and bosses.json is sorted so that a lookup by display name reaches the alpha first.

These tests read the built dataset rather than a fixture. The join is the thing being
tested, and a hand-written fixture would only prove that the join works on data written
to make it work.

The nine pairs below are written out rather than derived so this file catches the
*dataset* changing, which is the failure that matters - a rebuild that silently dropped
a pair would otherwise still pass.
"""
from __future__ import annotations

import json

import pytest

from palintel import cards, counters
from palintel.knowledge import REPO, KnowledgeBase
from palintel.routing import StubRouter
from palintel.tools import Decline, ToolCall

# The nine, as `pal_names_flat.json` states them. Eight are also in DT_UniqueNPCText;
# Zenara & Astralym is the one the text table has no key for, which is why reading only
# that table concluded Astralym's tower had no leader.
PAIRS = [("Victor", "Shadowbeak"), ("Zoe", "Grizzbolt"), ("Lily", "Lyleen"),
         ("Axel", "Orserk"), ("Marcus", "Faleris"), ("Saya", "Selyne"),
         ("Bjorn", "Bastigor"), ("Auri", "Shaolong"), ("Zenara", "Astralym")]
# The one pair with a single source. Everything else is confirmed by both tables.
UNCORROBORATED = {"Zenara"}


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def bosses() -> dict:
    path = REPO / "data" / "1.0.2" / "bosses.json"
    if not path.exists():
        pytest.skip("bosses.json not built")
    return json.loads(path.read_text(encoding="utf-8"))


def test_all_eight_pairs_are_ingested(bosses):
    got = {(l["leader"], l["pal"]) for l in bosses["leaders"]}
    assert got == set(PAIRS)


def test_every_leader_points_at_a_tier_one_gym_row(bosses):
    """Not `_2` (the same fight, harder) and not `_BossRush` (another mode)."""
    by_cid = {e["character_id"]: e for e in bosses["entries"]}
    for lead in bosses["leaders"]:
        row = by_cid[lead["character_id"]]
        assert row["character_id"].startswith("GYM_")
        assert row["tier"] == 1
        assert row["mode"] is None
        assert row["kind"] == "tower"


def test_every_tower_has_a_leader(bosses):
    """Nine pairs, nine towers, no orphans. An orphan means a patch added or renamed a
    tower, and the build stops on it rather than publishing a tower with no human."""
    assert bosses["towers_without_a_leader"] == []


def test_the_two_tables_agree_wherever_both_have_a_key(bosses):
    """**The check that makes this a fact rather than an inference.**

    `pal_names_flat.json` states the pair in one string and `DT_UniqueNPCText` splits it
    across two rows under a different key shape. They share no assumption, so agreement
    is real corroboration - and a disagreement would be the well-formed-and-wrong failure
    this project is organised against: the wrong human beside the right Pal looks
    entirely reasonable.
    """
    for lead in bosses["leaders"]:
        assert lead["corroborated"] is (lead["leader"] not in UNCORROBORATED)


def test_the_build_publishes_no_unmatched_leaders(bosses):
    assert bosses["leaders_unmatched"] == []


# ------------------------------------------------------------------ resolution

@pytest.mark.parametrize("leader, pal",
                         [p for p in PAIRS if p[0] != "Zenara"])
def test_a_leader_resolves_to_the_tower_not_the_alpha(leader, pal):
    """**The test this file exists for.**

    Both fights are called Shadowbeak. Resolving Victor through the display name would
    reach `BOSS_BlackGriffon`, a field alpha at a different level in a different place,
    and the card would look entirely correct.
    """
    plan = counters.plan(leader, None)
    assert plan.kind == "tower"
    assert plan.boss_id.startswith("GYM_")
    assert plan.boss_name == pal
    assert plan.leader == leader
    assert plan.leader_derived


def test_zenara_resolves_and_then_declines_for_the_right_reason(bosses):
    """Astralym is the one tower that cannot be countered by type, and the reason is in
    the pak rather than in this code: every `WorldTreeDragon` row carries
    `ElementType::None`. Resolving the leader and *then* declining is the correct
    sequence - it proves the mapping works and that nothing invented an element to fill
    the hole. The message names Astralym, because "Zenara has no element" is nonsense
    about a person.
    """
    assert any(l["leader"] == "Zenara" for l in bosses["leaders"])
    with pytest.raises(counters.CounterError, match="Astralym has no element"):
        counters.plan("Zenara", None)


def test_the_games_own_name_for_the_fight_resolves_too():
    """"Victor & Shadowbeak" is a PAL_NAME_ row, so it is in the routers' Pal enum and a
    model can pick it. It is also the most explicit way there is to name a tower."""
    plan = counters.plan("Victor & Shadowbeak", None)
    assert plan.boss_id == "GYM_BlackGriffon"
    assert plan.leader == "Victor"


def test_the_pal_name_still_resolves_to_the_alpha():
    """The other half of the same distinction: asking about Shadowbeak by name is a
    question about the creature in the world, and that answer must not move."""
    plan = counters.plan("Shadowbeak", None)
    assert plan.kind == "alpha"
    assert plan.leader is None


def test_an_unnamed_tower_still_declines():
    """"The first tower" carries no ordinal anywhere in the pak, and the model is allowed
    to pass it through verbatim. Guessing which tower it means is the failure; declining
    is the feature."""
    with pytest.raises(counters.CounterError):
        counters.plan("the first tower", None)


# ------------------------------------------------------------------ the lexicon

def test_leaders_are_their_own_kind_not_pal_aliases(kb: KnowledgeBase):
    """Aliasing Victor onto Shadowbeak was the shorter route and it loses the fight."""
    assert set(kb.lexicon.leaders()) == {l for l, _ in PAIRS}
    assert not set(kb.lexicon.leaders()) & set(kb.lexicon.pals())
    top = kb.lexicon.rank("how do I beat Victor")[0]
    assert (top.canonical, top.kind) == ("Victor", "leader")


def test_leaders_are_not_in_the_routers_pal_enum(kb: KnowledgeBase):
    """`pals()` generates the tool schemas' entity enum. Victor is not a species and must
    not become selectable as one by find_pal_spawns or find_pal_drops."""
    assert "Victor" not in kb.lexicon.pals()


# ------------------------------------------------------------------ routing

@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, counters=True, counterable={"anubis"})


def route(router, text):
    return router.route(text, router._lexicon.rank(text), [])


def test_the_fast_path_routes_a_leader(router):
    call = route(router, "hey pal how do I beat Victor")
    assert isinstance(call, ToolCall)
    assert call.name == "plan_counters"
    assert call.args == {"boss": "Victor"}


def test_a_leader_never_chains_a_location_call(router):
    """`find_pal_spawns` takes a species and there is no Victor in the spawn table, so
    the chain that fires for "where can I find something to beat Anubis" must not fire
    here - it would put a decline card beside a good answer."""
    call = route(router, "hey pal where do I find Victor to beat him")
    assert isinstance(call, ToolCall)
    assert call.then is None


def test_a_leader_without_a_counter_cue_is_not_claimed(router):
    """"Who is Victor" is not a counter question, and the branch has no other reading of
    a leader to offer."""
    assert isinstance(route(router, "hey pal who is Victor"), Decline)


# ------------------------------------------------------------------ the card

def test_the_card_names_the_pair_the_way_the_game_does():
    card = cards.counter_card(counters.plan("Victor", None))
    assert card.title == "How to fight Victor & Shadowbeak"
    # "Victor's tower", not "tower boss": there are nine towers and the generic label
    # cannot tell the player which one this is.
    assert "Victor's tower" in card.lines[0]


def test_a_double_sourced_pairing_is_not_caveated():
    """The footnote used to fire on every leader, from a reading in which the pairing was
    inferred. It is not - two independent tables state it - and caveating a fact that
    well sourced on every card makes the caveat mean nothing when it matters."""
    card = cards.counter_card(counters.plan("Victor", None))
    assert "only one source" not in card.footer


def test_a_card_with_no_leader_carries_no_leader_note():
    card = cards.counter_card(counters.plan("Anubis", None))
    assert "pairing" not in card.footer
