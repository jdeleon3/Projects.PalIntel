""""Also drops from" — the other way to get a resource.

The line earns its place on a locations card because it is most useful exactly when the
locations are not: the nearest coal may sit in a level 40 zone, and farming a Blazamut is
a route available at a level where walking there is not.

Two of these pin judgements made during ingest rather than behaviour, because both change
what the line *claims* and neither is visible from the card.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from palintel.cards import MAX_DROPPERS, resource_card
from palintel.execution import ResourceResult, find_resource_nodes
from palintel.knowledge import Dropper, KnowledgeBase

DATA = Path(__file__).resolve().parents[1] / "data" / "1.0.2"


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


def _result(droppers: list[Dropper]) -> ResourceResult:
    return ResourceResult(resource="coal", nodes=[], near=None, level_filtered=False,
                          total_available=0, droppers=droppers)


# ------------------------------------------------------------------------ the card

def test_a_resource_nothing_drops_gets_no_line(kb: KnowledgeBase):
    """Stone, wood and the World Tree materials have no dropper at all.

    An empty "Also drops from:" would read as missing data rather than as the truth,
    which is that nothing drops it.
    """
    card = resource_card(find_resource_nodes(kb, "stone", limit=3))
    assert not any("drops from" in line for line in card.lines)


def test_the_line_is_capped_and_says_how_many_it_hid(kb: KnowledgeBase):
    """Ore has 8 droppers. Naming all of them stops being a line."""
    card = resource_card(find_resource_nodes(kb, "ore", limit=3))
    line = next(l for l in card.lines if "drops from" in l)
    assert line.count("**") // 2 == MAX_DROPPERS
    assert "more" in line


def test_a_dead_end_card_still_offers_the_other_route(kb: KnowledgeBase):
    """No reachable coal is exactly when "a Blazamut drops 10" is worth saying.

    Gating at level 1 empties the result. Without this the card is a dead end; with it,
    it still answers the question the player actually asked, which was how to get coal.
    """
    empty = find_resource_nodes(kb, "coal", max_player_level=1, limit=3)
    assert not empty.nodes
    card = resource_card(empty)
    assert any("drops from" in line for line in card.lines)
    assert "Blazamut" in "\n".join(card.lines)


def test_an_alpha_only_dropper_says_so():
    """A different fight, so a different claim.

    No published dropper is currently alpha-only; the field exists so a patch that
    introduces one cannot quietly promise an ordinary encounter.
    """
    line = _dropper_line(_result([Dropper(pal="Blazamut", rate=100.0, low=10, high=10,
                                          alpha_only=True)]))
    assert "alpha" in line


def test_an_ordinary_dropper_does_not():
    line = _dropper_line(_result([Dropper(pal="Pierdon", rate=100.0, low=4, high=5)]))
    assert "alpha" not in line
    assert "4-5" in line, "the amount is what a player plans a trip around"


def _dropper_line(result: ResourceResult) -> str:
    return next(l for l in resource_card(result).lines if "drops from" in l)


# ------------------------------------------------------------- the ingest judgements

@pytest.fixture(scope="module")
def published() -> dict:
    path = DATA / "pal_drops.json"
    if not path.exists():
        pytest.skip("no drops dataset - run tools/ingest/build_pal_drops.py")
    return json.loads(path.read_text(encoding="utf-8"))


def test_no_published_dropper_has_a_zero_rate(published: dict):
    """The table carries real Rate-0 rows: Smokie Cryst for coal, Neptilius for quartz.

    Whatever they are upstream, they are not drops that happen. Naming one would put a
    Pal on the card that never yields the item - a fabricated value in a slot the player
    would act on, which is the one thing Tier 1 must never do.
    """
    for resource, droppers in published["by_item"].items():
        for d in droppers:
            assert d["rate"] > 0, f"{resource}: {d['pal']} published at rate 0"


def test_every_alpha_only_claim_is_flagged(published: dict):
    """Crediting `BOSS_RockBeast` to RockBeast is an inference from the naming.

    It was load-bearing for *nothing* while the dataset covered only the 18 locatable
    resources - zero droppers were alpha-only. Widening to all 151 items changed that:
    **705 of 1,990 claims (35%) rest on it**, concentrated exactly where the game would
    put them. Ancient Civilization Parts is 290/290 alpha-only; every rifle and armour
    schematic is a boss drop; Flame Organ, Leather and Wool are 0%. That distribution is
    evidence the inference is right, not that it stopped mattering.

    So the invariant is no longer "none exist" but "none is silent": an alpha-only claim
    must carry the flag that makes the card say `alpha`, because "Celesdir Noct drops
    Ancient Civilization Parts" is only true of the alpha and a player reading it as an
    ordinary encounter would farm the wrong thing.
    """
    flagged = 0
    for item, droppers in published["by_item"].items():
        for d in droppers:
            assert isinstance(d.get("alpha_only"), bool), (
                f"{item}: {d['pal']} has no alpha_only verdict, so the card cannot "
                f"tell the reader which kind of encounter this drop needs")
            flagged += d["alpha_only"]
    assert flagged, "expected some alpha-only droppers once all items are covered"


def test_a_common_material_is_not_alpha_gated(published: dict):
    """A sanity check on the inference, not on the plumbing.

    If ordinary Wool or Leather ever came back alpha-only, the variant collapse would be
    crediting base species with drops only their bosses have - the failure direction that
    matters, because it understates nothing and overstates the fight.
    """
    for item in ("Wool", "Leather", "Flame Organ"):
        droppers = published["by_item"].get(item)
        if not droppers:
            continue
        assert not all(d["alpha_only"] for d in droppers), (
            f"every {item} dropper is marked alpha-only, which is not how the game "
            f"works - check VARIANT_PREFIX in build_pal_drops.py")


def test_the_rules_are_published_with_the_data(published: dict):
    """A derived dataset has to carry the rules that derived it."""
    assert set(published["rules"]) >= {"rate_zero", "variant_collapse",
                                       "scenario_actors", "placeholder_names"}


def test_a_missing_dataset_does_not_break_the_knowledge_base(tmp_path: Path):
    """The drops file is optional; Q1 answers without it, just without the line."""
    kb = KnowledgeBase(game_version="1.0.2", lexicon=None)
    assert kb.droppers == {}


# ------------------------------------------------------------------ level bands

def test_level_bands_are_kept_apart(kb: KnowledgeBase):
    """DT_PalDropItem is banded, and collapsing the bands published a wrong answer.

    `WeaselDragon000` drops Leather and an Ice Organ; `WeaselDragon080` drops 30-50
    Ancient Relics. Taking the max across rows put the level-80 haul on the card as if an
    ordinary Chillet carried it - the same conflation of two encounter kinds that
    `alpha_only` exists to prevent, one level down. 128 of 890 characters are banded.
    """
    from palintel.execution import find_pal_drops

    r = find_pal_drops(kb, "Chillet")
    ordinary = {d.item for d in r.ordinary}
    assert ordinary == {"Ice Organ", "Leather"}, (
        f"an ordinary Chillet drops leather and an ice organ, not {sorted(ordinary)}")
    assert all(d.min_level == 0 for d in r.ordinary)
    assert any(d.min_level >= 70 for d in r.high_level)
    assert "Decayed Ancient Relic" in {d.item for d in r.high_level}


def test_the_card_labels_the_band(kb: KnowledgeBase):
    """A player reads the heading, not the schema."""
    from palintel.cards import drops_card
    from palintel.execution import find_pal_drops

    lines = drops_card(find_pal_drops(kb, "Chillet")).lines
    assert any(l.startswith("__Level ") and "only__" in l for l in lines)
    # The endgame haul must not appear above that heading.
    cut = next(i for i, l in enumerate(lines) if l.startswith("__Level "))
    assert not any("Ancient Relic" in l for l in lines[:cut])


def test_a_pal_with_no_drops_is_not_a_missing_row(kb: KnowledgeBase):
    """"Drops nothing" and "I have no data" are different answers."""
    from palintel.cards import drops_card
    from palintel.execution import find_pal_drops

    unknown = find_pal_drops(kb, "NotAPalAtAll")
    assert not unknown.known
    assert "isn't in the drop table" in " ".join(drops_card(unknown).lines)


# ------------------------------------------------------- the consolidated tool

def test_a_two_pal_question_keeps_both():
    """"What do I get from Astralym and Mycora" is two answers, not one.

    `find_pal_drops(pal)` has a single slot, so the per-class schema could not express
    this and no amount of prompt work would have fixed it - the measurement found a
    prompt that chose the right tool and still missed. The consolidated tool's `pals`
    array can hold both, and `unpack` carries the extras rather than discarding them.
    """
    from palintel.routing_unified import unpack

    name, args = unpack("answer_query", {"query_class": "pal_drops",
                                         "pals": ["Astralym", "Mycora"],
                                         "resources": [], "items_named": [],
                                         "target": None, "max_player_level": None})
    assert name == "find_pal_drops"
    assert args["pals"] == ["Astralym", "Mycora"]
    # The first still lands in the old slot, so a dispatcher that only knows `pal` works.
    assert args["pal"] == "Astralym"


def test_one_pal_needs_no_extra_slot():
    from palintel.routing_unified import unpack

    _, args = unpack("answer_query", {"query_class": "pal_drops", "pals": ["Chillet"],
                                      "resources": [], "items_named": [],
                                      "target": None, "max_player_level": None})
    assert args == {"pal": "Chillet"}


def test_an_item_question_reaches_the_item_slot():
    """Items live in the tool enum and NOT in the lexicon.

    Arrow, Bone, Leather and Horn are ordinary English; ranking them in the corrector
    would pull spurious candidates into queries that name no item at all. The router
    reaches for this slot on sentence context instead.
    """
    from palintel.routing_unified import unpack

    name, args = unpack("answer_query", {"query_class": "item_source", "pals": [],
                                         "resources": [], "items_named": ["Flame Organ"],
                                         "target": None, "max_player_level": None})
    assert (name, args) == ("find_item_source", {"item": "Flame Organ"})


def test_the_line_names_each_pal_once(kb: KnowledgeBase):
    """The dataset keys droppers by (pal, level band); the card is about species.

    Pierdon Cryst drops Pure Quartz at level 0 and again at level 70, which is two true
    rows and one Pal. Nine of the eleven resources with droppers hit this.
    """
    from palintel.cards import resource_card
    from palintel.execution import find_resource_nodes

    for resource in ("quartz", "coal", "ore"):
        line = next((l for l in resource_card(
            find_resource_nodes(kb, resource, limit=3)).lines if "drops from" in l), None)
        if line is None:
            continue
        named = [chunk for chunk in line.split("**") if chunk.strip()
                 and not chunk.startswith(" (")]
        pals = [n for i, n in enumerate(named) if i % 2 == 0][1:]
        assert len(pals) == len(set(pals)), f"{resource}: repeated Pal in {line}"


# ------------------------------------------------------------------- the fast path

def test_the_stub_answers_a_plain_drop_question(kb: KnowledgeBase):
    """The latency bar needs under 5% of queries reaching the model, so every shipped
    class needs a deterministic path. "What does X drop" is as templated as "where can I
    find X"."""
    from palintel.routing import StubRouter
    from palintel.tools import ToolCall

    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, cues="wide")
    text = "hey pal what does Vanwyrms drop"
    call = stub.route(text, kb.lexicon.rank(text))
    assert isinstance(call, ToolCall)
    assert (call.name, call.args) == ("find_pal_drops", {"pal": "Vanwyrm"})


def test_the_drop_branch_runs_before_the_location_gate(kb: KnowledgeBase):
    """A drop question has no location cue by construction.

    The branch claimed exactly nothing until it moved above that gate - `where|nearest|
    find|...` matches none of "what does Vanwyrm drop", so the decline fired first.
    """
    from palintel.routing import _CUE_SETS, _DROP_CUES
    import re

    text = "what does Vanwyrm drop"
    assert _DROP_CUES.search(text)
    for name, pattern in _CUE_SETS.items():
        assert not re.search(rf"\b({pattern})\b", text, re.I), (
            f"the {name} location cues now overlap drop phrasing; the two branches "
            f"would fight over the same utterance")


def test_two_different_pals_go_to_the_model(kb: KnowledgeBase):
    """One slot, two answers. Deferring is right; answering half of it silently is not."""
    from palintel.routing import StubRouter
    from palintel.tools import Decline

    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, cues="wide")
    text = "what do I get from Astralym and Mycora"
    assert isinstance(stub.route(text, kb.lexicon.rank(text)), Decline)


def test_a_variant_is_not_a_second_pal(kb: KnowledgeBase):
    """"Incineram Noct" ranks Incineram beside it at the same score.

    Treating that as two entities would defer every variant query to the model for no
    reason - the dispatcher renders the family anyway.
    """
    from palintel.routing import StubRouter
    from palintel.tools import ToolCall

    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, cues="wide")
    text = "what does Incineroom Noct drop"
    call = stub.route(text, kb.lexicon.rank(text))
    assert isinstance(call, ToolCall) and call.name == "find_pal_drops"


def test_the_stub_still_declines_a_drop_question_with_no_pal(kb: KnowledgeBase):
    from palintel.routing import StubRouter
    from palintel.tools import Decline

    stub = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, cues="wide")
    text = "what drops flame organs"
    assert isinstance(stub.route(text, kb.lexicon.rank(text)), Decline)
