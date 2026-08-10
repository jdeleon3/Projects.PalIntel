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
