"""Per-player state — M1 of Docs/multi-user-design.md.

The failure this exists to prevent is **cross-attribution**: answering Alice's question
from Bob's save. It is the multi-user version of the failure this project refuses to ship,
because every number on the card is real and nothing about it looks wrong.

The numbers here are from the two-player world measured on 2026-08-13
(`44403D774601FB7B22EA0C83E1A16FE5`): `Rui` at 35 technologies and 83/7 points,
`OutofLuck` at 61 and 59/8. Fixtures rather than the save itself, for the reason at the
top of test_saves.py - but the values are real, so a test passing here means the thing
that would have gone wrong in play does not.
"""
from __future__ import annotations

import time

import pytest

from palintel.identity import Bindings, resolve
from palintel.saves import PlayerSnapshot, SaveWatcher

RUI = "00000000-0000-0000-0000-000000000001"
LUCK = "48f23c66-0000-0000-0000-000000000000"


def _snap(uid, coords, techs, points, ancient):
    return PlayerSnapshot(
        uid=uid, map_coords=coords, world=(0.0, 0.0, 0.0),
        technologies=frozenset(f"t{i}" for i in range(techs)),
        transform_id="test", read_at=time.time(), written_at=time.time(),
        points=points, ancient_points=ancient)


@pytest.fixture
def coop(tmp_path):
    """A watcher holding the real two-player world's divergent state."""
    (tmp_path / "Players").mkdir()
    w = SaveWatcher(tmp_path)
    w.snapshots = {
        RUI: _snap(RUI, (228.0, -485.0), 35, 83, 7),
        LUCK: _snap(LUCK, (230.0, -486.0), 61, 59, 8),
    }
    w.players = {RUI: "Rui", LUCK: "OutofLuck"}
    w.snapshot = None          # two players: nothing is unambiguous
    return w


# --- the binding store --------------------------------------------------------

def test_a_binding_survives_a_restart(tmp_path):
    """Rebinding four people after every restart is how a feature stops being used."""
    path = tmp_path / "players.json"
    Bindings(path).bind("111", RUI, display_name="Ruichan", nickname="Rui")
    assert Bindings(path).uid_for("111") == RUI


def test_a_corrupt_store_is_not_fatal(tmp_path):
    """Losing bindings costs one `/palintel iam` each and degrades to world-scoped
    answers, which is the honest state. Raising would take the bot down over a
    convenience file."""
    path = tmp_path / "players.json"
    path.write_text("{ not json", encoding="utf-8")
    assert len(Bindings(path)) == 0


def test_rebinding_replaces_rather_than_accumulates(tmp_path):
    b = Bindings(tmp_path / "players.json")
    b.bind("111", RUI, nickname="Rui")
    b.bind("111", LUCK, nickname="OutofLuck")
    assert b.uid_for("111") == LUCK
    assert len(b) == 1


# --- resolution ---------------------------------------------------------------

def test_a_bound_speaker_resolves_to_their_own_player(tmp_path):
    b = Bindings(tmp_path / "players.json")
    b.bind("111", LUCK, nickname="OutofLuck")
    uid, why = resolve(b, "111", {RUI: "Rui", LUCK: "OutofLuck"})
    assert uid == LUCK and why == "bound"


def test_an_unbound_speaker_in_a_coop_world_resolves_to_nobody(tmp_path):
    """**The whole point.** Not "fall back to the host" - that is the cross-attribution
    this prevents. None means answer about the world and say so."""
    b = Bindings(tmp_path / "players.json")
    uid, why = resolve(b, "999", {RUI: "Rui", LUCK: "OutofLuck"})
    assert uid is None and why == "unbound"


def test_a_single_player_world_needs_no_binding(tmp_path):
    """Attribute when unambiguous. One player means one possible answer for everybody, so
    single-player behaviour is untouched and nobody has to bind to keep it."""
    b = Bindings(tmp_path / "players.json")
    uid, why = resolve(b, "999", {RUI: "Rui"})
    assert uid == RUI and "only player" in why


def test_a_binding_to_a_player_who_left_resolves_to_nobody(tmp_path):
    """A player who is gone is not the same as a player we can guess at."""
    b = Bindings(tmp_path / "players.json")
    b.bind("111", LUCK, nickname="OutofLuck")
    uid, why = resolve(b, "111", {RUI: "Rui"})
    assert uid is None and "doesn't have" in why


# --- per-player state, which is M1's exit criterion ----------------------------

def test_two_players_get_their_own_technology_state(coop):
    """The exit criterion from the design, with the real world's numbers.

    Technology is the most divergent thing in a co-op save, and Q6 is set arithmetic over
    it. Answered from the wrong player this produces a shopping list against someone
    else's tree and someone else's budget, on a card that looks entirely correct.
    """
    rui, luck = coop.player_tech(RUI), coop.player_tech(LUCK)
    assert len(rui.unlocked) == 35 and rui.points == 83 and rui.ancient_points == 7
    assert len(luck.unlocked) == 61 and luck.points == 59 and luck.ancient_points == 8


def test_an_unplaceable_speaker_gets_an_empty_state_not_someone_elses(coop):
    """`unlocked=None` is "never read", which `progression_card` declines on. An empty
    SET would instead recommend tier-1 research to a level-57 player - well-formed,
    plausible and wrong."""
    state = coop.state_for(None)
    assert state.player_coords is None
    assert state.tech is None or state.tech.unlocked is None
    assert state.owned_species is None


def test_each_player_gets_their_own_position(coop):
    assert coop.coords_for(RUI) == (228.0, -485.0)
    assert coop.coords_for(LUCK) == (230.0, -486.0)


def test_an_unknown_uid_gets_nothing_rather_than_a_fallback(coop):
    assert coop.coords_for("nobody") is None
    assert coop.player_tech("nobody").unlocked is None


def test_a_coop_world_has_no_unambiguous_snapshot(coop):
    """`snapshot` is the single-player convenience and must go None the moment there are
    two, or every caller that still reads it silently picks one of them."""
    assert coop.snapshot is None
    assert coop.player_coords() is None


def test_status_lists_every_player_rather_than_naming_one(coop):
    line = coop.describe()
    assert "2 players" in line
    assert "Rui" in line and "OutofLuck" in line


def test_the_staleness_gate_is_per_player(coop):
    """One player idle for a day must not cost the other their position."""
    coop.snapshots[RUI] = _snap(RUI, (228.0, -485.0), 35, 83, 7)
    object.__setattr__(coop.snapshots[RUI], "written_at", time.time() - 86400)
    assert coop.coords_for(RUI) is None
    assert coop.coords_for(LUCK) == (230.0, -486.0)
