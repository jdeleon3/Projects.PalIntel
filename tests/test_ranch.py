"""The ranch line — the one card fact that is not extracted from the game.

Every other value on a spawn card comes from the pak. These come from a community wiki,
because the mapping is in blueprint bytecode and none of the 284 data tables carries it
(ADR-0014's amendment). That makes attribution and the unverified marker load-bearing
rather than cosmetic: a card that showed a wiki claim in the same voice as a coordinate
would be overstating one of them.
"""
from __future__ import annotations

import pytest

from palintel.cards import MAX_RANCH_ITEMS, spawn_card
from palintel.execution import SpawnResult, find_pal_spawns
from palintel.knowledge import KnowledgeBase, Ranch, RanchDrop

SOURCE = "https://palworld.wiki.gg/wiki/Ranch"


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase.load("1.0.2")


def _result(ranch: Ranch | None, *, in_overworld: bool = True,
            source: str = SOURCE) -> SpawnResult:
    return SpawnResult(pal="Testmon", areas=[], near=None, kind="normal",
                       kind_substituted=False, total_available=0,
                       in_overworld=in_overworld, ranch=ranch, ranch_source=source)


def _ranch_line(card) -> str | None:
    return next((l for l in card.lines if l.startswith("Ranch:")), None)


def test_a_pal_that_cannot_be_ranched_gets_no_line(kb: KnowledgeBase):
    card = spawn_card(find_pal_spawns(kb, "Chillet"))
    assert _ranch_line(card) is None
    assert not any("ranch data" in l for l in card.lines)


def test_a_ranchable_pal_names_what_it_makes(kb: KnowledgeBase):
    card = spawn_card(find_pal_spawns(kb, "Lamball"))
    assert "Wool" in _ranch_line(card)


def test_every_ranch_line_is_marked_unofficial():
    """Not a hedge - the point of the line.

    This is the only fact on a Tier 1 card that is not extracted from the game files,
    and it must not read in the same voice as the coordinates above it. The marker
    replaced a full source URL, which repeated the same address on every ranchable
    Pal's card; attribution still travels on the dataset.
    """
    card = spawn_card(_result(Ranch(drops=[RanchDrop("Wool")], per_cycle=1, food=1)))
    assert "(unofficial)" in _ranch_line(card)
    assert not any(SOURCE in l for l in card.lines), "the URL belongs on the data"


def test_an_uncorroborated_entry_escalates_the_same_marker():
    """Mau Cryst is on the wiki with no matching asset in the pak.

    Shown, because the roster is not a complete authority either - but the caveat
    sharpens the existing parenthetical rather than adding a second one.
    """
    card = spawn_card(_result(Ranch(drops=[RanchDrop("Ice Organ")], per_cycle=1,
                                    food=1, verified=False)))
    line = _ranch_line(card)
    assert "unofficial" in line
    assert "don't list this one as ranchable" in line


def test_a_corroborated_entry_carries_only_the_plain_marker():
    line = _ranch_line(spawn_card(
        _result(Ranch(drops=[RanchDrop("Wool")], per_cycle=1, food=1))))
    assert "ranchable" not in line


def test_the_line_is_capped_and_counts_the_rest(kb: KnowledgeBase):
    """Vixy produces seven things; the card is read at a glance."""
    card = spawn_card(find_pal_spawns(kb, "Vixy"))
    line = _ranch_line(card)
    assert line.count("**") // 2 == MAX_RANCH_ITEMS
    assert "+4 more" in line


def test_quantities_and_odds_survive_to_the_card():
    ranch = Ranch(drops=[RanchDrop("Gold Coin", stack=100, chance_percent=20)],
                  per_cycle=4, food=3)
    line = _ranch_line(spawn_card(_result(ranch)))
    assert "x100" in line and "20%" in line


def test_the_line_shows_even_when_the_pal_has_no_wild_spawn():
    """"I can't tell you where to find it" is exactly when "you can ranch it" helps."""
    ranch = Ranch(drops=[RanchDrop("Honey")], per_cycle=3, food=3)
    card = spawn_card(_result(ranch, in_overworld=False))
    assert "Honey" in _ranch_line(card)


def test_missing_ranch_data_is_not_an_error(kb: KnowledgeBase):
    """The dataset is optional; spawn answers are complete without it."""
    empty = KnowledgeBase(game_version="1.0.2", lexicon=kb.lexicon)
    assert empty.ranch == {} and empty.ranch_source == ""
    assert _ranch_line(spawn_card(_result(None, source=""))) is None
