"""Save reading.

The interesting cases are the failures, not the happy path. A real save is a moving
target - the codec already changed once in a minor version - so what has to hold is that
every way of failing degrades one word of one answer instead of taking the bot down.

Fixtures are synthesised rather than checked in: a real Palworld save is over a megabyte
and contains the account id of whoever produced it.
"""
from __future__ import annotations

import json
import os
import struct
import time
import zlib
from pathlib import Path

import pytest

from palintel.saves import (SaveError, SaveWatcher, Transform, decompress,
                            newest_player_save)


def container(payload: bytes, magic: bytes = b"PlZ", save_type: int = 0x31,
              body: bytes | None = None) -> bytes:
    body = zlib.compress(payload) if body is None else body
    return struct.pack("<II", len(payload), len(body)) + magic + bytes([save_type]) + body


# --- the container ---------------------------------------------------------------

def test_single_and_double_compression_both_unwrap():
    assert decompress(container(b"GVAS-ish")) == b"GVAS-ish"
    inner = zlib.compress(b"GVAS-ish")
    assert decompress(container(inner, save_type=0x32)) == b"GVAS-ish"


def test_a_truncated_body_is_named_as_such():
    """The failure that actually happens: reading while the game is mid-write.

    The header still parses and the magic is still right, so without the length check
    this reaches zlib and reports a corrupt stream - which reads like a damaged save
    rather than a race, and sends the reader looking in the wrong place.
    """
    raw = container(b"payload")
    with pytest.raises(SaveError, match="being written"):
        decompress(raw[:-4])


def test_unknown_codec_names_the_magic():
    with pytest.raises(SaveError, match="PlX"):
        decompress(container(b"x", magic=b"PlX"))


def test_a_stub_file_is_not_read_as_a_header():
    with pytest.raises(SaveError, match="truncated"):
        decompress(b"tiny")


# --- the transform ---------------------------------------------------------------

def test_transform_swaps_axes():
    """Map X comes from world Y. Getting this backwards yields plausible coordinates."""
    t = Transform("test", scale=100.0, offset_x=0.0, offset_y=0.0)
    assert t.to_map(500.0, 200.0) == (2.0, 5.0)


def test_shipped_transform_places_a_known_node_where_the_data_says():
    """Guards the shipped constants against a silent edit.

    The node file stores world and map coordinates for the same deposit, computed by the
    extraction pipeline, so re-deriving one from the other is an end-to-end check that
    this module agrees with the data it will be compared against.
    """
    nodes = json.loads(Path("data/1.0.2/resource_nodes.json").read_text(encoding="utf-8"))
    recs = nodes["clusters"] if "clusters" in nodes else nodes["nodes"]
    t = Transform.load()
    for r in recs[:50]:
        mx, my = t.to_map(r["world"]["x"], r["world"]["y"])
        assert abs(mx - r["map_x"]) < 1.0 and abs(my - r["map_y"]) < 1.0


# --- the watcher -----------------------------------------------------------------

def test_missing_player_directory_is_reported_not_raised(tmp_path):
    w = SaveWatcher(tmp_path)
    assert w.poll() is False
    assert w.player_coords() is None
    assert "no player save" in w.error


def test_a_bad_save_keeps_the_previous_position(tmp_path, monkeypatch):
    """The whole point of the degradation path.

    A save that fails to parse - a format change, a torn read - must leave the last known
    position in place. Dropping to None would silently turn "nearest" back into "biggest"
    with nothing in the answer to say so.
    """
    players = tmp_path / "Players"
    players.mkdir()
    save = players / "0001.sav"
    save.write_bytes(container(b"good"))

    w = SaveWatcher(tmp_path)
    monkeypatch.setattr("palintel.saves.read_player",
                        lambda p, t=None: _snapshot((10.0, 20.0)))
    assert w.poll() is True
    assert w.player_coords() == (10.0, 20.0)

    def boom(path, transform=None):
        raise SaveError("torn read")

    monkeypatch.setattr("palintel.saves.read_player", boom)
    save.write_bytes(container(b"changed"))
    # Stamped explicitly: two writes in the same test land inside st_mtime's resolution,
    # so the watcher would correctly see no change and the test would pass for the wrong
    # reason. Real autosaves are minutes apart.
    os.utime(save, (time.time() + 10, time.time() + 10))
    assert w.poll() is False
    assert w.player_coords() == (10.0, 20.0)      # the old fix survives
    assert "torn read" in w.error


def test_a_failed_read_is_retried_rather_than_waited_out(tmp_path, monkeypatch):
    """A torn read must not consume the file's change.

    Recording the mtime before a successful parse would mean one unlucky read leaves the
    position stale until the *next* autosave, minutes later.
    """
    players = tmp_path / "Players"
    players.mkdir()
    (players / "0001.sav").write_bytes(container(b"x"))

    w = SaveWatcher(tmp_path)
    calls = []

    def flaky(path, transform=None):
        calls.append(path)
        if len(calls) == 1:
            raise SaveError("torn read")
        return _snapshot((1.0, 2.0))

    monkeypatch.setattr("palintel.saves.read_player", flaky)
    assert w.poll() is False
    assert w.poll() is True                        # same file, no new write needed
    assert w.player_coords() == (1.0, 2.0)


def test_the_newest_player_save_wins(tmp_path):
    """Co-op leaves a save per player; the local one is the one being written."""

    players = tmp_path / "Players"
    players.mkdir()
    old, new = players / "aaa.sav", players / "bbb.sav"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (time.time() - 500, time.time() - 500))
    assert newest_player_save(tmp_path) == new


def _snapshot(coords, written_at=None):
    from palintel.saves import PlayerSnapshot
    return PlayerSnapshot(uid="u", map_coords=coords, world=(0.0, 0.0, 0.0),
                          technologies=frozenset(), transform_id="test", read_at=0.0,
                          written_at=time.time() if written_at is None else written_at)


# --- the staleness gate -------------------------------------------------------
#
# Nothing checked how old a save was until 2026-08-13. With the game closed the bot
# answered "where's the nearest coal" against a position from whenever the file was last
# written, and the status card said "read 3s ago" - the wrong clock, reading as
# reassurance. A coordinate card built from a stale position is byte-for-byte identical
# to one built from a live position, in the one class that sends the player somewhere.

def _watcher(tmp_path, **kw):
    from palintel.saves import SaveWatcher
    (tmp_path / "Players").mkdir(exist_ok=True)
    return SaveWatcher(tmp_path, **kw)


def test_a_fresh_position_is_offered(tmp_path):
    w = _watcher(tmp_path, max_position_age=900)
    w.snapshot = _snapshot((10.0, 20.0), written_at=time.time() - 60)
    assert w.player_coords() == (10.0, 20.0)


def test_a_stale_position_is_withheld_rather_than_offered(tmp_path):
    """The whole point. Every card already knows how to answer without a position -
    resource lookup ranks by cluster size and says so - and none of them could tell that
    the position they were given was a fortnight old."""
    w = _watcher(tmp_path, max_position_age=900)
    w.snapshot = _snapshot((10.0, 20.0), written_at=time.time() - 14 * 86400)
    assert w.player_coords() is None
    assert w.position_age() > 900


def test_the_boundary_is_generous_enough_for_an_autosave_gap(tmp_path):
    """A player sitting still between autosaves must not lose 'nearest'.

    MAX_POSITION_AGE is a bound rather than a calibration - nobody has recorded
    Palworld's write cadence - so it is deliberately far longer than any plausible
    interval. This pins the direction of the error: the gate exists to catch a save that
    stopped updating, not to second-guess a normal gap between writes.
    """
    w = _watcher(tmp_path, max_position_age=900)
    w.snapshot = _snapshot((10.0, 20.0), written_at=time.time() - 890)
    assert w.player_coords() == (10.0, 20.0)


def test_an_unknown_write_time_does_not_read_as_ancient(tmp_path):
    """No mtime means we do not KNOW the age, which is different from knowing it is old.

    Treating a missing mtime as stale would withhold a position over a failed `stat`;
    treating it as zero would be the confident lie the field exists to prevent. `age()`
    returns None and the position is still offered, with the status line saying so.
    """
    w = _watcher(tmp_path, max_position_age=900)
    w.snapshot = _snapshot((10.0, 20.0), written_at=0.0)
    assert w.snapshot.age() is None
    assert w.player_coords() == (10.0, 20.0)
    assert "save age unknown" in w.describe()


def test_status_reports_the_save_clock_not_the_read_clock(tmp_path):
    """`read_at` was 3 seconds on a save written a fortnight ago. True, useless, and
    reassuring in exactly the situation where someone is checking because it looks
    broken."""
    w = _watcher(tmp_path, max_position_age=900)
    w.snapshot = _snapshot((10.0, 20.0), written_at=time.time() - 14 * 86400)
    line = w.describe()
    assert "14d" in line
    assert "too old to use" in line
    # The read clock would have said "0s ago" here, which is what made this invisible.
    assert "read 0s ago" not in line


def test_the_roster_is_not_gated_on_position_age(tmp_path):
    """Slow-moving state stays usable when the position does not.

    You catch a Pal every few minutes at best and move a base almost never, so an
    hour-old roster is still worth filtering a counter card by. Gating it on the position
    bound would throw away good answers to prevent an error it cannot make - and it is
    exactly what makes a synced or shared save still worth reading.
    """
    w = _watcher(tmp_path, max_position_age=900)
    w.snapshot = _snapshot((10.0, 20.0), written_at=time.time() - 14 * 86400)
    w.roster = frozenset({"lamball"})
    w.base_camps = [(1.0, 2.0)]
    assert w.player_coords() is None
    assert w.roster == frozenset({"lamball"})
    assert w.base_camps == [(1.0, 2.0)]


# --- owned Pal roster ---------------------------------------------------------
#
# Fixtures are hand-built UE property bytes rather than a real Level.sav, for the
# reason at the top of this file and because the blob is the only part that matters.

def name_property(name: bytes, value: bytes) -> bytes:
    """`<name><NameProperty><size:i64><has_guid:u8><len:i32><value>` - the layout a
    regex over this blob gets wrong by returning the type tag."""
    def s(b: bytes) -> bytes:
        return struct.pack("<i", len(b) + 1) + b + b"\x00"
    payload = s(value)
    return s(name) + s(b"NameProperty") + struct.pack("<q", len(payload)) + b"\x00" + payload


def test_character_id_reads_the_value_not_the_type_tag():
    """The bug this parser exists to avoid: 554 of 555 entries "matched" NameProperty."""
    from palintel.saves import _character_id
    assert _character_id(name_property(b"CharacterID", b"CuteFox")) == "CuteFox"


def test_character_id_absent_is_none_not_a_guess():
    from palintel.saves import _character_id
    assert _character_id(b"\x00\x01nothing useful here\x00") is None


def test_character_id_ignores_a_distant_name_property():
    """A NameProperty far downstream belongs to a different property."""
    from palintel.saves import _character_id
    blob = b"CharacterID\x00" + b"\x00" * 64 + name_property(b"Nickname", b"Fluffy")
    assert _character_id(blob) is None


def test_blob_accepts_a_tuple_of_ints():
    """The GVAS reader returns a tuple; an isinstance(list) check silently returns
    nothing for every entry in the save."""
    from palintel.saves import _blob
    assert _blob({"values": tuple(b"abc")}) == b"abc"
    assert _blob({"values": list(b"abc")}) == b"abc"
    assert _blob(b"abc") == b"abc"
    assert _blob({"values": "not bytes"}) is None


def test_owned_species_lowercases_so_the_pak_join_survives_casing():
    """The save writes `Sheepball`, the pak writes `SheepBall`. A case-sensitive join
    drops the Pal with no error - it just goes missing from the owned set."""
    from palintel.saves import _character_id
    from_save = _character_id(name_property(b"CharacterID", b"Sheepball"))
    from_pak = "SheepBall"
    assert from_save != from_pak
    assert from_save.lower() == from_pak.lower()
