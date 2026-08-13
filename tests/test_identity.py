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
from palintel.saves import PlayerSnapshot, Rosters, SaveWatcher, _le_guid, _slot_instance

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


# --- M2: the roster, per player and per guild ---------------------------------
#
# Numbers from the same two worlds. The solo one is the important assertion: the change
# is behaviour-preserving there, so it can be checked against a total that already
# existed rather than against a judgement.

import struct                                                       # noqa: E402
import uuid                                                         # noqa: E402


def test_a_slot_names_the_character_it_holds():
    """Slot RawData is PlayerUId(16) + InstanceId(16) + 6, read off real slots."""
    player = uuid.UUID("00000000-0000-0000-0000-000000000001")
    inst = uuid.UUID("4dabb608-4319-cdf2-c0ed-febf0d881363")

    def le(u):
        return struct.pack("<4I", *struct.unpack(">4I", u.bytes))

    assert _slot_instance(le(player) + le(inst) + b"\x00" * 6) == str(inst)


def test_an_empty_slot_holds_nobody():
    assert _slot_instance(b"\x00" * 38) is None
    assert _slot_instance(b"\x00" * 8) is None


def test_guid_byte_order_is_little_endian_uint32():
    """Reading this big-endian yields a valid-looking Guid that joins to nothing."""
    assert _le_guid(bytes.fromhex("00000000" "00000000" "00000000" "01000000")) == RUI


def _rosters(carried, shared, everyone):
    return Rosters(carried={k: frozenset(v) for k, v in carried.items()},
                   shared=frozenset(shared), everyone=frozenset(everyone))


def test_the_solo_roster_is_unchanged_by_the_split():
    """**M2's exit criterion.** 184 carried + 11 species held only in base containers =
    the 195 the union always returned. Any other number is the trap: filtering on
    OwnerPlayerUId alone silently drops base-camp Pals, six of them ordinary Pals the
    counter card would shortlist."""
    carried = {f"c{i}" for i in range(184)}
    only_shared = {f"s{i}" for i in range(11)}
    r = _rosters({RUI: carried}, carried | only_shared, carried | only_shared)
    assert len(r.everyone) == 195
    assert len(r.for_player(RUI)) == 195


def test_neither_coop_player_is_given_the_union():
    """`Rui` owns 32 and `OutofLuck` 39 of a 53-species world. Handing either of them 53
    is a 66% over-count, and the counter card would shortlist Pals the other player has."""
    rui = {f"r{i}" for i in range(13)} | {f"both{i}" for i in range(19)}
    luck = {f"l{i}" for i in range(20)} | {f"both{i}" for i in range(19)}
    # 8 shared species, and on the real save only ONE of them is found nowhere else -
    # base Pals are mostly duplicates of ones somebody also carries. 52 carried + 1 = 53.
    shared = {f"both{i}" for i in range(7)} | {"only-in-base"}
    r = _rosters({RUI: rui, LUCK: luck}, shared, rui | luck | shared)
    assert len(r.for_player(RUI)) == 32 + 1      # 7 of the 8 he already carries
    assert len(r.for_player(LUCK)) == 39 + 1
    assert len(r.everyone) == 53
    assert r.for_player(RUI) != r.for_player(LUCK)
    # Neither is handed the other's carried Pals, which is the over-count itself.
    assert not (r.for_player(RUI) & {f"l{i}" for i in range(20)})


def test_guild_pals_reach_every_member():
    """Base Pals are shared by construction - that is what a guild is - so both members
    field them and neither is told they own the other's carried Pals."""
    r = _rosters({RUI: {"lamball"}, LUCK: {"cattiva"}}, {"anubis"},
                 {"lamball", "cattiva", "anubis"})
    assert "anubis" in r.for_player(RUI) and "anubis" in r.for_player(LUCK)
    assert "cattiva" not in r.for_player(RUI)


def test_an_unattributable_speaker_gets_none_not_the_union():
    """None stays None to the card, which says it has not looked. The union would be a
    claim about Pals that are somebody else's."""
    r = _rosters({RUI: {"lamball"}}, set(), {"lamball"})
    assert r.for_player(None) is None
    assert r.for_player("someone-who-just-joined") is None


def test_the_watcher_does_not_hand_a_coop_player_the_world_roster(coop):
    coop.rosters = _rosters({RUI: {"lamball"}, LUCK: {"cattiva"}}, {"anubis"},
                            {"lamball", "cattiva", "anubis"})
    coop.roster = coop.rosters.everyone
    assert coop.roster_for(RUI) == frozenset({"lamball", "anubis"})
    assert coop.roster_for(None) is None
    assert coop.state_for(RUI).owned_species == frozenset({"lamball", "anubis"})


def test_a_single_player_world_still_gets_its_roster_before_the_split_is_read(tmp_path):
    """The per-player read is on a slow timer, so there is a window where only the world
    roster exists. With one player the two sets are the same by construction, so
    answering from it is not a guess."""
    (tmp_path / "Players").mkdir()
    w = SaveWatcher(tmp_path)
    w.snapshots = {RUI: _snap(RUI, (1.0, 2.0), 10, 5, 1)}
    w.roster = frozenset({"lamball"})
    assert w.roster_for(RUI) == frozenset({"lamball"})


def test_status_reports_each_player_rather_than_one_total(coop):
    coop.rosters = _rosters({RUI: {"lamball"}, LUCK: {"cattiva"}}, {"anubis"},
                            {"lamball", "cattiva", "anubis"})
    coop.roster = coop.rosters.everyone
    line = coop.describe_roster()
    assert "Rui 2" in line and "OutofLuck 2" in line
    assert "in the world" in line
