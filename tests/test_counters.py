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


# --- the card ----------------------------------------------------------------

from palintel.cards import TIER_ADVICE, TIER_FACT, counter_card  # noqa: E402
from palintel.counters import CounterResult  # noqa: E402


def result(**kw) -> CounterResult:
    base = dict(boss_id="RAID_NightLady", boss_name="Bellanoir", name_derived=True,
                kind="raid", boss_elements=("Dark",), level=35,
                candidates=[m("elphidran", "Elphidran", ["Dragon"], STRONG, WEAK)],
                owned_considered=192, counter_elements=("Dragon",))
    return CounterResult(**{**base, **kw})


def test_a_counter_card_is_advice_not_fact():
    """Amber, not green. Every other card reports extracted facts; this one reports a
    computation over them, and a player who cannot tell will trust both equally."""
    assert counter_card(result()).colour == TIER_ADVICE
    assert TIER_ADVICE != TIER_FACT


def test_the_card_says_when_the_boss_name_was_inferred():
    """No table names a GYM_/RAID_/BOSS_ row, so asserting one silently would be the
    derived-rule failure CLAUDE.md names."""
    assert "inferred" in counter_card(result()).footer
    assert "inferred" not in counter_card(result(name_derived=False)).footer


def test_owning_nothing_effective_still_names_what_would_work():
    """"No" and "not yet" are different answers and cost the same to compute."""
    card = counter_card(result(candidates=[]))
    text = card.to_text()
    assert "Nothing you own" in text
    assert "Dragon" in text


def test_element_names_are_the_ones_the_player_sees():
    """The pak says Leaf, Electricity, Earth; the game shows Grass, Electric, Ground.
    A card printing the internal spelling is describing a table, not answering."""
    card = counter_card(result(boss_elements=("Leaf",), counter_elements=("Fire",),
                               candidates=[]))
    assert "Grass" in card.to_text() and "Leaf" not in card.to_text()


def test_neutral_defense_is_not_printed():
    """"takes 1x" on every line hides the lines that matter."""
    card = counter_card(result(
        candidates=[m("a", "A", ["Dragon"], STRONG, NEUTRAL)]))
    assert "takes" not in card.to_text()


def test_a_punishing_matchup_is_flagged():
    card = counter_card(result(
        candidates=[m("a", "A", ["Dragon"], STRONG, STRONG)]))
    assert "takes double" in card.to_text()


def test_the_level_is_shown_when_known_and_omitted_when_not():
    """Raid bosses have a level from the raid table; tower bosses have none in any of
    the 530 data tables, and inventing one would be worse than omitting it."""
    assert "level 35" in counter_card(result()).to_text()
    assert "level" not in counter_card(result(level=None, kind="tower")).to_text()


def test_an_all_tied_shortlist_says_the_order_is_arbitrary():
    """Typing is the only thing scored, so against a single-element boss every counter
    is 2x/0.5x and the order falls out alphabetically. Presenting that as a ranking
    asserts something the data does not say."""
    card = counter_card(result(candidates=[
        m("a", "A", ["Dragon"], STRONG, WEAK), m("b", "B", ["Dragon"], STRONG, WEAK)]))
    assert "order is arbitrary" in card.footer


def test_a_differentiated_shortlist_does_not():
    card = counter_card(result(candidates=[
        m("a", "A", ["Dragon"], STRONG, WEAK), m("b", "B", ["Dragon"], STRONG, STRONG)]))
    assert "arbitrary" not in card.footer


def test_a_single_candidate_is_not_called_arbitrary():
    card = counter_card(result(candidates=[m("a", "A", ["Dragon"], STRONG, WEAK)]))
    assert "arbitrary" not in card.footer
