"""The element matrix.

This is the project's second community-sourced dataset, and unlike ranch outputs it is
checkable against itself: effectiveness is an involution, so a mistyped cell breaks a
pairing. These tests exist to prove the check actually catches things - a validator that
only ever sees correct input is indistinguishable from one that returns [].
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "ingest"))

from build_elements import ALIASES, canon, parse_wiki, validate  # noqa: E402

WIKI = REPO / "data" / "raw" / "elements_wiki.md"
BUILT = REPO / "data" / "1.0.2" / "elements.json"

# The nine the pak's ElementType1/2 actually use.
PAK_ELEMENTS = {"Normal", "Fire", "Water", "Electricity", "Leaf", "Dark", "Dragon",
                "Ice", "Earth"}


def good() -> dict[str, dict]:
    """A correct matrix, written out rather than loaded, so a bad cached dataset
    cannot make these tests pass."""
    m = {
        "Dark": (["Normal"], ["Dragon"]),
        "Dragon": (["Dark"], ["Ice"]),
        "Electricity": (["Water"], ["Earth"]),
        "Fire": (["Leaf", "Ice"], ["Water"]),
        "Leaf": (["Earth"], ["Fire"]),
        "Earth": (["Electricity"], ["Leaf"]),
        "Ice": (["Dragon"], ["Fire"]),
        "Normal": ([], ["Dark"]),
        "Water": (["Fire"], ["Electricity"]),
    }
    return {k: {"strong_against": s, "weak_against": w} for k, (s, w) in m.items()}


def test_a_correct_matrix_validates():
    assert validate(good(), PAK_ELEMENTS) == []


def test_a_broken_involution_is_caught():
    """The failure mode of copying a 9x9 grid by hand: one cell, one direction."""
    m = good()
    m["Water"]["strong_against"] = ["Leaf"]      # Water no longer strong vs Fire
    errs = validate(m, PAK_ELEMENTS)
    assert any("Water strong vs Leaf" in e for e in errs)
    assert any("Fire weak vs Water" in e for e in errs)


def test_a_missing_weakness_is_caught():
    m = good()
    m["Ice"]["weak_against"] = []
    assert any("Ice: 0 weaknesses" in e for e in validate(m, PAK_ELEMENTS))


def test_an_element_the_pak_uses_but_the_matrix_omits_is_caught():
    m = good()
    del m["Dragon"]
    assert any("no matrix row" in e and "Dragon" in e
               for e in validate(m, PAK_ELEMENTS))


def test_self_matchup_is_caught():
    m = good()
    m["Fire"]["strong_against"] = ["Fire", "Ice"]
    assert any("matched against itself" in e for e in validate(m, PAK_ELEMENTS))


def test_exactly_one_element_is_strong_against_two():
    """Stated in prose on the source page, so it is worth pinning separately."""
    m = good()
    m["Water"]["strong_against"] = ["Fire", "Earth"]
    assert any("strong against two" in e for e in validate(m, PAK_ELEMENTS))


def test_wiki_names_are_aliased_to_pak_names():
    """Four of nine differ, and a fuzzy matcher would be wrong about which four."""
    assert canon("Neutral") == "Normal"
    assert canon("Grass") == "Leaf"
    assert canon("Electric") == "Electricity"
    assert canon("Ground") == "Earth"
    assert canon("Fire") == "Fire"
    assert set(ALIASES) == {"neutral", "grass", "electric", "ground"}


@pytest.mark.skipif(not WIKI.exists(), reason="wiki page not cached")
def test_the_cached_page_still_parses_to_a_valid_matrix():
    """Guards the real risk with a cached source: the page layout changes and the
    parser silently yields fewer rows."""
    m = parse_wiki(WIKI)
    assert set(m) == PAK_ELEMENTS
    assert validate(m, PAK_ELEMENTS) == []
