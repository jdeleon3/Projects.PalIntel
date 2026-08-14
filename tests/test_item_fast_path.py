"""The `item_source` fast path — a SECOND lexicon, ranked only when an item cue fires.

`item_source` had zero fast-path coverage until this branch: every query paid the full
model round trip (see `spend.py`'s own note that it should dominate the ledger for
exactly that reason). These tests are mostly about the SEPARATION this needed to be safe:
item names are ordinary English and 12 of the 18 resources share a display name with a
droppable/craftable item, so the item lexicon must never be allowed to answer for a
location or counter question, and vice versa.
"""
from __future__ import annotations

import pytest

from palintel.knowledge import KnowledgeBase, Lexicon
from palintel.routing import StubRouter
from palintel.tools import Decline


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


@pytest.fixture(scope="module")
def router(kb: KnowledgeBase) -> StubRouter:
    return StubRouter(kb.lexicon, {n.resource for n in kb.nodes},
                      item_lexicon=kb.item_lexicon)


def call(router, text):
    return router.route(text, router._lexicon.rank(text), [])


# --------------------------------------------------------------- the Lexicon extension

def test_item_lexicon_loads_as_a_separate_instance(kb: KnowledgeBase):
    assert kb.item_lexicon is not None
    assert isinstance(kb.item_lexicon, Lexicon)
    assert kb.item_lexicon.pals() == []
    assert kb.item_lexicon.resources() == []
    assert len(kb.item_lexicon.item_names()) > 900


def test_the_main_lexicon_carries_no_items(kb: KnowledgeBase):
    """The whole point: items must never reach the ranked list every other branch
    trusts. `Candidate.kind` would need to grow an "item" case everywhere it is checked
    if this broke, and nothing does."""
    assert kb.lexicon.item_names() == []


def test_an_absent_item_lexicon_turns_the_branch_off(kb: KnowledgeBase):
    r = StubRouter(kb.lexicon, {n.resource for n in kb.nodes}, item_lexicon=None)
    assert getattr(call(r, "who drops flame organ"), "name", None) != "find_item_source"


# ------------------------------------------------------------------------- the branch

def test_a_drop_question_about_an_item_is_claimed(router):
    c = call(router, "who drops flame organ")
    assert c.name == "find_item_source" and c.args["item"] == "Flame Organ"


def test_a_craft_question_is_claimed(router):
    c = call(router, "what do I need for a cake")
    assert c.name == "find_item_source" and c.args["item"] == "Cake"


def test_how_do_i_make_is_claimed(router):
    c = call(router, "how do I craft a cake")
    assert c.name == "find_item_source" and c.args["item"] == "Cake"


def test_where_do_i_get_is_claimed(router):
    c = call(router, "where do I get leather")
    assert c.name == "find_item_source" and c.args["item"] == "Leather"


def test_no_cue_defers(router):
    """Naming an item with no drop/craft cue is not enough - "I have a bone" names
    Bone but asks nothing."""
    assert getattr(call(router, "I have a bone"), "name", None) != "find_item_source"


def test_a_weak_match_defers_rather_than_guesses(router):
    """Below ITEM_CONFIDENT, decline rather than claim - the cue alone is not license
    to guess which item. Reproduces a real ranking gap: `rank()` caps at 3-grams, so a
    four-word canonical name like "High Quality Pal Oil" scores below its own 2-word
    prefix match on "high quality X" phrasings and several sibling items outrank it. The
    floor turning that into a decline, not a wrong pick, is the point of this test."""
    c = call(router, "where do I get high quality pal oil")
    assert getattr(c, "name", None) != "find_item_source"


# ----------------------------------------------------------- does not steal from others

def test_a_resource_location_question_is_not_claimed(router):
    """"Where can I find coal" must still answer with mining locations - coal is one of
    the 12 resources that also drops from Pals, and the item branch must not win this
    phrasing just because the entity is shared."""
    c = call(router, "where can I find coal")
    assert c.name == "find_resource_nodes" and c.args["resource"] == "coal"


def test_paldium_fragment_as_a_drop_question_goes_to_item_source(router):
    """The other direction of the same collision: "what drops paldium fragment" is
    asking about DROPPERS, not mine locations, and the drop cue must win here - the two
    branches are disjoint by cue, not by entity."""
    c = call(router, "what drops paldium fragment")
    assert c.name == "find_item_source" and c.args["item"] == "Paldium Fragment"


def test_a_pal_location_question_is_not_claimed(router):
    c = call(router, "where can I find Anubis")
    assert c.name == "find_pal_spawns"


def test_a_pal_drop_question_is_not_claimed_as_an_item_question(router):
    """"What does Vanwyrm drop" ranks a confident Pal in `candidates`, and `_drops_call`
    sits above `_item_call` in dispatch order - see routing.py's comment on why. Vanwyrm
    is not a plausible item-lexicon match either, so this is belt and braces."""
    c = call(router, "what does Vanwyrm drop")
    assert c.name == "find_pal_drops" and c.args["pal"] == "Vanwyrm"


def test_two_confident_items_defers(router):
    """One slot, two named items - "who drops bone and leather" - is the same shape
    `_drops_call` guards against for two Pals. Both must independently clear the floor
    for the guard to fire; a cue phrase is not itself a second item."""
    c = call(router, "who drops bone and leather")
    assert getattr(c, "name", None) != "find_item_source"


def test_a_confident_pal_reading_defers_even_over_a_stronger_item_score(router):
    """Reproduces the one theft the first measurement sweep found. "What do I get from
    Gildra and Fuddler" ranks both as real Pals at 1.00 - `_drops_call` correctly defers,
    it is a two-Pal question - but "Gildra" also fuzzy-matches "Giga Glider" at 0.95 in
    the item lexicon, a coincidence of letters. The Pal reading must win regardless of
    which score is higher: it is independent, stronger evidence about what the utterance
    means, not a competing entity to arbitrate by score."""
    c = call(router, "what do I get from Gildra and Fuddler")
    assert getattr(c, "name", None) != "find_item_source"


def test_a_near_miss_sibling_item_does_not_trigger_the_two_item_guard(router):
    """Reproduces the coverage regression the fix for the theft above nearly caused.
    "Ancient Civilization Parts" scores 1.00 and "Ancient Civilization Core" scores 0.905
    right behind it on the SAME phrase - not a second named item, one item with a
    similarly-prefixed sibling. The two-item guard must key on the two candidates
    matching DISJOINT spans of the utterance, not merely both clearing the floor."""
    c = call(router, "who drops ancient civilization parts")
    assert c.name == "find_item_source"
    assert c.args["item"] == "Ancient Civilization Parts"


# ------------------------------------------------------------------- end to end (M0-ish)

from palintel.pipeline import Pipeline, PlayerState  # noqa: E402


def test_the_fast_path_renders_a_real_card_with_no_model_call(kb: KnowledgeBase,
                                                               router: StubRouter):
    p = Pipeline(kb, router)
    out = p.handle("what do I need for a cake", PlayerState())
    assert not isinstance(out.call, Decline)
    assert out.call.usage is None, "the fast path must not have paid for a model call"
    assert "Flour" in out.card.to_text()
