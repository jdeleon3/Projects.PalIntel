"""Multi-card answers — one card per entity, a clarifying question past the cap.

A Paldeck slot holds a base Pal and its element variant (Menasting / Menasting Terra =
DarkScorpion / DarkScorpion_Ground). They differ in element, spawns and breeding, so a
query that cannot be narrowed to one of them has *two correct answers*, not one ambiguous
one. On a second screen that is two titled cards; past two it becomes a wall, and asking
is better than answering everything.

These test the shape rather than the trigger: Q1 registers one resource tool and a
resource query names one resource, so the multi-card path activates with `find_pal_spawns`
in Phase 2. The structure is verified now because `Outcome` had to change either way.
"""
from __future__ import annotations

import pytest

from palintel.cards import Card, clarify_card
from palintel.knowledge import KnowledgeBase
from palintel.pipeline import MAX_CARDS, Outcome, Pipeline, PlayerState
from palintel.routing import StubRouter
from palintel.tools import Decline


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


def _card(title: str) -> Card:
    return Card(title=title, lines=["x"])


def test_outcome_card_returns_the_first(kb: KnowledgeBase):
    """Callers that only ever show one card keep working."""
    o = Outcome([_card("A"), _card("B")], Decline(reason="x"), [])
    assert o.card.title == "A"
    assert len(o.cards) == 2


def test_a_resource_answer_is_one_card(kb: KnowledgeBase):
    pipe = Pipeline(kb, StubRouter(kb.lexicon, {n.resource for n in kb.nodes}))
    out = pipe.handle("where's the nearest coal", PlayerState())
    assert len(out.cards) == 1


def test_a_decline_is_one_card(kb: KnowledgeBase):
    pipe = Pipeline(kb, StubRouter(kb.lexicon, {n.resource for n in kb.nodes}))
    out = pipe.handle("what should I research next", PlayerState())
    assert len(out.cards) == 1
    assert isinstance(out.call, Decline)


def test_family_sized_answer_renders_one_card_each(kb: KnowledgeBase):
    pipe = Pipeline(kb, StubRouter(kb.lexicon, set()))
    got = pipe._cards_for(["Menasting", "Menasting Terra"], lambda e: _card(e))
    assert [c.title for c in got] == ["Menasting", "Menasting Terra"]


def test_past_the_cap_it_asks_instead_of_answering(kb: KnowledgeBase):
    pipe = Pipeline(kb, StubRouter(kb.lexicon, set()))
    many = ["Menasting", "Menasting Terra", "Solmora", "Solmora Lux"]
    got = pipe._cards_for(many, lambda e: _card(e))
    assert len(got) == 1
    assert got[0].title == "Which one?"
    # The options have to be named, or the reader cannot act on the question.
    for name in many:
        assert name in "\n".join(got[0].lines)


def test_variant_families_never_exceed_the_cap(kb: KnowledgeBase):
    """The cap only binds on multi-entity queries, never on a single family.

    If a future lexicon ever ships a three-member slot this fails, which is the point:
    the cap was chosen knowing families are pairs.
    """
    biggest = max(len(kb.lexicon.family(p)) for p in kb.lexicon.pals())
    assert biggest <= MAX_CARDS


def test_clarify_card_is_not_a_decline(kb: KnowledgeBase):
    """Nothing failed - the query was understood, the entity was not narrowed. The card
    should ask a specific question rather than apologise."""
    c = clarify_card(["Menasting", "Menasting Terra"])
    text = c.to_text().lower()
    assert "which one" in text
    assert "didn't catch" not in text
