"""The boss dataset.

Every test here is a trap that was found by looking at real rows, not by reasoning about
what the data should contain. That is the point of the file: each one drops or mangles a
boss silently, and none of them raises anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "ingest"))

from build_bosses import MODE, PART, TIER, strip_prefix  # noqa: E402


def test_prefix_matching_is_case_insensitive():
    """Boss_Anubis is 1 row of 323 spelled differently, and startswith("BOSS_") drops
    exactly one Pal - the one the play protocol asks about by name."""
    assert strip_prefix("BOSS_Alpaca") == "Alpaca"
    assert strip_prefix("Boss_Anubis") == "Anubis"
    assert strip_prefix("GYM_ElecPanda") == "ElecPanda"
    assert strip_prefix("RAID_NightLady") == "NightLady"


def test_a_non_boss_id_has_no_prefix_to_strip():
    """None, not the id itself - an ordinary Pal must not look like its own alpha."""
    assert strip_prefix("Alpaca") is None
    assert strip_prefix("LazyDragon") is None


def test_body_parts_are_recognised():
    """"What counters Moon Lord's left hand" is not a question."""
    for part in ("RAID_YakushimaBoss002_Hand_Left", "RAID_YakushimaBoss002_Head",
                 "RAID_YakushimaBoss002_Hand_Right_2"):
        assert PART.search(part), part
    assert not PART.search("RAID_NightLady")
    assert not PART.search("BOSS_Alpaca")


def test_tier_suffix_is_the_same_fight_not_another_boss():
    assert TIER.search("ElecPanda_2").group(1) == "2"
    assert TIER.search("NightLady_Dark_2").group(1) == "2"
    assert TIER.search("ElecPanda") is None


def test_mode_suffix_is_stripped_before_the_base_join():
    """BOSS_BlackGriffon_BossRush is the same creature in another mode. Left in place
    the base-tribe join simply fails and the boss goes out unnamed."""
    assert MODE.search("BlackGriffon_BossRush").group(1) == "BossRush"
    assert MODE.sub("", "BlackGriffon_BossRush") == "BlackGriffon"
    assert MODE.search("BlackGriffon") is None


def test_tier_and_mode_compose():
    """`_BossRush` and `_2` can both appear, and stripping one must not strand the other."""
    base = MODE.sub("", TIER.sub("", "ElecPanda_BossRush"))
    assert base == "ElecPanda"
