"""The labels on the breeding verification sheet.

The sheet is read by a human standing in front of the game, so its failure mode is not a
crash - it is sending someone to catch the wrong Pal, or to attempt a row they cannot
reach. Both happened before these were added.

**The variant trap is the reason the number is worth printing at all.** A variant does not
get its own Paldeck number; it gets the base Pal's number plus a letter. Printing
`zukan_index` alone would label Eidrolon and Eidrolon Ignis both `#171`, and this sheet is
unusually dense with exactly those pairs.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "eval"))
import make_breeding_predictions as mk  # noqa: E402

SHEET = REPO / "Docs" / "breeding-verification.md"


# --- the number ----------------------------------------------------------------

def test_a_variant_keeps_the_base_number_and_gains_a_letter():
    assert mk.paldeck_number({"zukan_index": 171, "zukan_suffix": ""}) == "#171"
    assert mk.paldeck_number({"zukan_index": 171, "zukan_suffix": "B"}) == "#171B"


def test_the_number_is_zero_padded_so_it_matches_the_paldeck():
    assert mk.paldeck_number({"zukan_index": 5, "zukan_suffix": "B"}) == "#005B"


@pytest.mark.parametrize("idx", [None, -1])
def test_a_pal_outside_the_paldeck_gets_no_number_rather_than_a_fake_one(idx):
    """-1 is what the pak stores for raid bosses and the 450-odd rows that are not
    catchable Pals. A `#-01` would be worse than nothing."""
    assert mk.paldeck_number({"zukan_index": idx, "zukan_suffix": ""}) == ""


# --- the level -----------------------------------------------------------------

def test_alpha_areas_do_not_set_the_catch_level(tmp_path, monkeypatch):
    """An alpha is one fixed high-level encounter, not how a breeding parent is obtained.
    Counting it would raise the floor for Pals catchable much earlier - and the whole
    point of the column is deciding whether a row is reachable."""
    import json

    d = tmp_path / "data" / "1.0.2"
    d.mkdir(parents=True)
    (d / "pal_spawns.json").write_text(json.dumps({"areas": [
        {"pal": "Chillet", "kind": "alpha", "level_min": 70},
        {"pal": "Chillet", "kind": "ordinary", "level_min": 22},
        {"pal": "Chillet", "kind": "ordinary", "level_min": 31},
    ]}), encoding="utf-8")
    monkeypatch.setattr(mk, "REPO", tmp_path)
    assert mk.catch_levels("1.0.2") == {"Chillet": 22}, "lowest ORDINARY spawn"


def test_a_pal_with_no_ordinary_spawn_is_absent_not_zero(tmp_path, monkeypatch):
    """Celesdir Noct and Moldron Cryst are breed-only. That is a fact about the Pal, and a
    0 would read as "catchable at level 0"."""
    import json

    d = tmp_path / "data" / "1.0.2"
    d.mkdir(parents=True)
    (d / "pal_spawns.json").write_text(json.dumps({"areas": [
        {"pal": "Celesdir Noct", "kind": "alpha", "level_min": 50},
    ]}), encoding="utf-8")
    monkeypatch.setattr(mk, "REPO", tmp_path)
    assert "Celesdir Noct" not in mk.catch_levels("1.0.2")


# --- the generated sheet -------------------------------------------------------

def _table_cells() -> list[str]:
    out = []
    for line in SHEET.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        for cell in line.split("|")[1:-1]:
            c = re.sub(r"[*_`]", "", cell).strip()
            if c and c not in ("Parent A", "Parent B", "Predicted child",
                               "Actual", "Nearest by rank (skipped)"):
                out.append(c)
    return out


def test_every_pal_on_the_sheet_carries_a_number():
    """Regression for the two-tier gap: names resolved from both `breeding.json` and the
    pak table, numbers from only the first, so Shadowbeak - which reaches the sheet from
    the exception table as `BlackGriffon` - printed bare between eight numbered rows and
    looked like missing data about that Pal."""
    bare = [c for c in _table_cells() if not c.startswith("#")]
    assert not bare, f"unnumbered Pals on the sheet: {sorted(set(bare))}"


def test_every_pal_carries_a_level_or_says_it_cannot_be_caught():
    cells = _table_cells()
    assert cells, "sheet has no table rows - did generation fail?"
    bad = [c for c in cells if not re.search(r"\((lv \d+|bred only)\)$", c)]
    assert not bad, f"no catch level: {sorted(set(bad))[:10]}"


def test_the_sheet_warns_that_block_1_needs_an_endgame_roster():
    """The measurement that reversed the sheet's own advice: 14 of Block 1's 19 Pals spawn
    only above level 60, under a heading telling the tester to do it first."""
    text = SHEET.read_text(encoding="utf-8")
    assert "start with Block 4 instead" in text
    assert "level 60" in text


def test_the_variant_letter_is_explained_where_the_tester_will_read_it():
    """`#171` and `#171B` differ by one character and are different Pals. Unexplained,
    the number makes the variant trap easier to fall into rather than harder."""
    text = SHEET.read_text(encoding="utf-8")
    assert "#171B" in text and "different Pals" in text
