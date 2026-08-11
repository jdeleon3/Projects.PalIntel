"""Q5 candidate sets and the Tier 2 guard.

The guard is the point. Every test that matters here is about something a model can do
that must not reach a card: name a Pal the player does not own, name one that is not
effective, invent one outright, or name the same one twice.
"""
from __future__ import annotations

import pytest

from palintel.counters import (NEUTRAL, STRONG, WEAK, CounterError, Matchup,
                               effectiveness, validate)

# The real matrix, written out so these tests do not depend on a built dataset.
MATRIX = {
    "Dark": {"strong_against": ["Normal"], "weak_against": ["Dragon"]},
    "Dragon": {"strong_against": ["Dark"], "weak_against": ["Ice"]},
    "Electricity": {"strong_against": ["Water"], "weak_against": ["Earth"]},
    "Fire": {"strong_against": ["Leaf", "Ice"], "weak_against": ["Water"]},
    "Leaf": {"strong_against": ["Earth"], "weak_against": ["Fire"]},
    "Earth": {"strong_against": ["Electricity"], "weak_against": ["Leaf"]},
    "Ice": {"strong_against": ["Dragon"], "weak_against": ["Fire"]},
    "Normal": {"strong_against": [], "weak_against": ["Dark"]},
    "Water": {"strong_against": ["Fire"], "weak_against": ["Electricity"]},
}


def m(cid, name, elements, offense, defense=NEUTRAL) -> Matchup:
    return Matchup(cid, name, tuple(elements), offense, defense)


def test_single_element_matchups():
    assert effectiveness(("Fire",), ("Leaf",), MATRIX) == STRONG
    assert effectiveness(("Fire",), ("Water",), MATRIX) == WEAK
    assert effectiveness(("Fire",), ("Dark",), MATRIX) == NEUTRAL


def test_same_element_is_neutral_not_strong():
    """Stated on the source page: a skill matching the target's element is unchanged."""
    assert effectiveness(("Fire",), ("Fire",), MATRIX) == NEUTRAL


def test_strong_and_weak_against_a_dual_element_pal_cancels():
    """The rule most likely to be got wrong, and it recommends a dud when it is.

    Fire is strong against Leaf and weak against Water, so against a Leaf/Water Pal it
    is 1x - not 2x.
    """
    assert effectiveness(("Fire",), ("Leaf", "Water"), MATRIX) == NEUTRAL


def test_double_strength_does_not_produce_four_times():
    """Fire hits both halves of a Leaf/Ice Pal. The game caps this at 2x."""
    assert effectiveness(("Fire",), ("Leaf", "Ice"), MATRIX) == STRONG


def test_an_unknown_element_raises_rather_than_scoring_neutral():
    """Silently returning 1x would rank a Pal as merely unremarkable instead of
    revealing that the matrix and the typing table disagree."""
    with pytest.raises(CounterError):
        effectiveness(("Plasma",), ("Fire",), MATRIX)


# --- the guard ---------------------------------------------------------------

def candidates() -> list[Matchup]:
    return [m("cutefox", "Vixy", ["Fire"], STRONG),
            m("sheepball", "Lamball", ["Normal"], STRONG)]


def test_a_pal_the_player_does_not_own_is_discarded():
    kept, discarded = validate(["Vixy", "Jetragon"], candidates())
    assert [k.name for k in kept] == ["Vixy"]
    assert discarded == ["Jetragon"]


def test_an_invented_pal_is_discarded():
    kept, discarded = validate(["Flamewyrm"], candidates())
    assert kept == []
    assert discarded == ["Flamewyrm"]


def test_matching_accepts_the_character_id_as_well_as_the_name():
    kept, _ = validate(["cutefox"], candidates())
    assert [k.name for k in kept] == ["Vixy"]


def test_matching_survives_the_save_versus_pak_casing():
    """The save writes `Sheepball`, the pak writes `SheepBall`. A case-sensitive check
    discards a legitimate pick."""
    kept, discarded = validate(["SheepBall", "LAMBALL"], candidates())
    assert discarded == []
    assert len(kept) == 1  # both refer to the same Pal


def test_the_same_pal_named_twice_appears_once():
    kept, _ = validate(["Vixy", "cutefox"], candidates())
    assert len(kept) == 1


def test_order_is_the_models_and_duplicates_do_not_reorder():
    kept, _ = validate(["Lamball", "Vixy"], candidates())
    assert [k.name for k in kept] == ["Lamball", "Vixy"]


def test_nothing_named_keeps_nothing():
    """An empty recommendation is honest; the candidate set is not a fallback that
    gets substituted in silently."""
    kept, discarded = validate([], candidates())
    assert kept == [] and discarded == []


def test_sort_key_puts_offence_first_then_survivability():
    strong_fragile = m("a", "A", ["Fire"], STRONG, defense=STRONG)
    strong_tanky = m("b", "B", ["Fire"], STRONG, defense=WEAK)
    assert strong_tanky.sort_key < strong_fragile.sort_key


def test_effective_means_better_than_neutral():
    assert m("a", "A", ["Fire"], STRONG).effective
    assert not m("b", "B", ["Fire"], NEUTRAL).effective
    assert not m("c", "C", ["Fire"], WEAK).effective
