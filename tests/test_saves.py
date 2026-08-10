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


def _snapshot(coords):
    from palintel.saves import PlayerSnapshot
    return PlayerSnapshot(uid="u", map_coords=coords, world=(0.0, 0.0, 0.0),
                          technologies=frozenset(), transform_id="test", read_at=0.0)
