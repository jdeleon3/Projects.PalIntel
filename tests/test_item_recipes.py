"""`item_source` gains a crafting half — the "where can I find cakes" fix.

Recorded in the roadmap: `item_source` answers from the DROP table alone, so a crafted
item gets a technically-true, practically-useless card - "Cake comes from Lovander, 1%" -
when the real answer is a recipe. These tests are mostly about that ordering: a recipe,
when one exists, must be the headline, not a footnote beside a drop rate nobody should farm.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from palintel.cards import item_source_card
from palintel.execution import ItemSourceResult, find_item_source
from palintel.knowledge import Dropper, Ingredient, KnowledgeBase, Recipe

DATA = Path(__file__).resolve().parents[1] / "data" / "1.0.2"


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


# ------------------------------------------------------------------- the execution layer

def test_cake_has_a_recipe(kb: KnowledgeBase):
    """The exact case the roadmap named: Flour, Red Berries, Milk, Egg, Honey."""
    result = find_item_source(kb, "Cake")
    assert result.craftable
    materials = {i.item: i.count for i in result.recipes[0].materials}
    assert materials == {"Flour": 5, "Red Berries": 8, "Milk": 7, "Egg": 8, "Honey": 2}


def test_cake_still_carries_its_drop_sources(kb: KnowledgeBase):
    """The recipe is the fix, not a replacement - the 1% Lovander drop is still a true
    fact and the card must not lose it, only stop leading with it."""
    result = find_item_source(kb, "Cake")
    assert result.total > 0
    assert any(d.pal == "Lovander" for d in result.ordinary + result.alpha_only)


def test_a_drop_only_item_has_no_recipe(kb: KnowledgeBase):
    """Leather drops from many Pals and is not crafted here - `craftable` must stay
    False rather than a stray recipe row appearing from nowhere."""
    result = find_item_source(kb, "Leather")
    assert result.known
    assert not result.craftable


def test_an_unknown_item_declines(kb: KnowledgeBase):
    result = find_item_source(kb, "Not A Real Item")
    assert not result.known
    assert not result.craftable


def test_cake_ranch_hints_join_to_verified_pals(kb: KnowledgeBase):
    """The roadmap's own join: Honey<-Beegarde, Red Berries<-Caprity, Egg<-Chikipi,
    Milk<-Mozzarina, all roster_verified. Not required for the fix to work - the recipe
    stands on its own - but it is cheap and it is what a player asks next."""
    result = find_item_source(kb, "Cake")
    expected = {"Honey": "Beegarde", "Red Berries": "Caprity",
               "Egg": "Chikipi", "Milk": "Mozzarina"}
    for item, pal in expected.items():
        assert result.ranch_hints.get(item) == (pal, True), item


# ------------------------------------------------------------------------------ the card

def _synthetic(**kw) -> ItemSourceResult:
    base = dict(item="Widget", ordinary=[], alpha_only=[], high_level=[], known=True,
               recipes=[], ranch_hints={})
    base.update(kw)
    return ItemSourceResult(**base)


def test_a_craftable_item_leads_with_the_recipe():
    result = _synthetic(recipes=[Recipe(product_count=1,
                                        materials=[Ingredient(item="Wood", count=3)],
                                        work_amount=100.0)])
    card = item_source_card(result)
    assert card.title == "Widget is crafted from"
    assert "Wood" in card.lines[0] and "x3" in card.lines[0]


def test_a_craftable_item_with_drops_shows_both_but_leads_with_the_recipe():
    """The bug this fixes, reproduced directly: a drop that is TRUE (Lovander, 1%) must
    not be what the title claims when a recipe also exists."""
    result = _synthetic(
        ordinary=[Dropper(pal="Lovander", rate=1.0, low=1, high=1)],
        recipes=[Recipe(product_count=1, materials=[Ingredient(item="Flour", count=5)],
                        work_amount=100.0)])
    card = item_source_card(result)
    assert card.title == "Widget is crafted from"
    text = "\n".join(card.lines)
    assert "Flour" in text
    assert "Lovander" in text, "the drop is still true and must still be on the card"
    # The recipe line comes first - the whole point of the fix.
    assert text.index("Flour") < text.index("Lovander")


def test_a_drop_only_item_still_says_comes_from():
    """Nothing about this class should change for the 100 items that only drop."""
    result = _synthetic(ordinary=[Dropper(pal="Chillet", rate=50.0, low=1, high=2)])
    card = item_source_card(result)
    assert card.title == "Widget comes from"
    assert "crafted" not in card.title


def test_a_completely_unknown_item_declines():
    result = _synthetic(known=False)
    card = item_source_card(result)
    assert "No source data" in card.title


def test_neither_drops_nor_crafts_says_so_precisely():
    """`known=True` with nothing in any list - the drop table has a row for the item, it
    is simply empty. Distinct from `known=False`, which is "never heard of it"."""
    card = item_source_card(_synthetic())
    assert "Nothing drops" in card.title
    assert "crafts it either" in card.lines[0]


def test_a_ranch_hint_flags_when_the_roster_does_not_corroborate_it():
    """The recipe is pak-stated; the ranch join is community-wiki (see `Ranch`'s own
    docstring). A verified hint names the Pal plainly; an unverified one says so, the same
    distinction `_ranch_lines` makes elsewhere on the card for a Pal's own ranch output."""
    verified = _synthetic(
        recipes=[Recipe(product_count=1, materials=[Ingredient(item="Honey", count=2)],
                        work_amount=100.0)],
        ranch_hints={"Honey": ("Beegarde", True)})
    line = item_source_card(verified).lines[0]
    assert "Beegarde" in line and "unverified" not in line

    unverified = _synthetic(
        recipes=[Recipe(product_count=1, materials=[Ingredient(item="Honey", count=2)],
                        work_amount=100.0)],
        ranch_hints={"Honey": ("Beegarde", False)})
    assert "unverified" in item_source_card(unverified).lines[0]


def test_multiple_recipes_show_the_cheapest_and_count_the_rest():
    result = _synthetic(recipes=[
        Recipe(product_count=1, materials=[Ingredient(item="Wood", count=1)],
              work_amount=10.0),
        Recipe(product_count=1, materials=[Ingredient(item="Stone", count=5)],
              work_amount=500.0),
    ])
    card = item_source_card(result)
    text = "\n".join(card.lines)
    assert "Wood" in text
    assert "Stone" not in text, "only the cheapest recipe's materials are shown"
    assert "1 other way" in text
    assert card.footer == "2 recipes"
