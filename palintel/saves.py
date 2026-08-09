"""Palworld save reading — where the player is, and what they have unlocked.

This exists to answer one word: *"nearest"*. Without it, `find_resource_nodes` falls
back to ranking clusters by deposit count, so "where's the nearest coal" returns the
biggest coal field on the map, which may be a continent away. That answer is confidently
wrong in exactly the way [ADR-0007](../Docs/adr/0007-answer-or-abstain.md) is about.

**The format is a moving target, and that is a demonstrated risk, not a hypothetical
one.** Palworld 0.6 changed the save codec from zlib (`PlZ`) to Oodle (`PlM`) in a minor
version. `palworld-save-tools` 0.24.0 handles only `PlZ`, so `decompress` here handles
both; the header framing is identical and only the codec differs.

Everything is best-effort by construction. A failed parse returns the last good state and
logs, because a save-format change must degrade the *quality* of an answer, never take
the bot down — [ADR-0005](../Docs/adr/0005-save-file-player-state.md)'s "state
unavailable" path is load-bearing, not decorative.

**Player level is not available here.** It lives in `Level.sav`'s
`CharacterSaveParameterMap` inside a `RawData` blob whose decoder in 0.24.0 is stale for
1.0.2 (it fails with "EOF not reached"). So `PlayerState.player_level` stays None and
level gating stays off; position, which is what "nearest" needs, comes from the player
save alone and needs no blob decoding at all.
"""
from __future__ import annotations

import json
import logging
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("palintel.saves")

REPO = Path(__file__).resolve().parents[1]
TRANSFORM_PATH = REPO / "data" / "coord_transform.json"

# Header: uncompressed length, compressed length, 3-byte codec magic, 1-byte save type.
HEADER = struct.Struct("<II")
HEADER_LEN = 12
SINGLE, DOUBLE = 0x31, 0x32     # ASCII "1" and "2" - one compression pass or two


class SaveError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transform:
    """UE world centimetres to in-game map coordinates.

    The axes swap: map X comes from world Y. Fitted and validated against held-out
    landmarks in Phase 0 (see data/coord_transform.json), and version-scoped - a map
    change in a future patch invalidates it, which is why `transform_id` travels with
    every coordinate rather than being assumed.
    """
    transform_id: str
    scale: float
    offset_x: float
    offset_y: float

    @classmethod
    def load(cls, path: Path = TRANSFORM_PATH) -> "Transform":
        raw = json.loads(path.read_text(encoding="utf-8"))
        m = raw["model"]
        return cls(raw["transform_id"], m["scale"], m["offset_x"], m["offset_y"])

    def to_map(self, world_x: float, world_y: float) -> tuple[float, float]:
        return ((world_y - self.offset_y) / self.scale,
                (world_x - self.offset_x) / self.scale)


@dataclass(frozen=True)
class PlayerSnapshot:
    uid: str
    map_coords: tuple[float, float]
    world: tuple[float, float, float]
    technologies: frozenset[str]
    transform_id: str
    read_at: float


def decompress(raw: bytes) -> bytes:
    """Unwrap a .sav container, zlib or Oodle.

    The length fields are checked rather than trusted. A save read while the game is
    mid-write is truncated but structurally valid-looking, and letting that reach the
    GVAS parser produces an error about property types that says nothing about the real
    cause.
    """
    if len(raw) < HEADER_LEN:
        raise SaveError(f"file is {len(raw)} bytes: truncated or not a save")

    uncompressed_len, compressed_len = HEADER.unpack(raw[:8])
    magic, save_type = raw[8:11], raw[11]
    body = raw[HEADER_LEN:]

    if magic not in (b"PlZ", b"PlM"):
        raise SaveError(f"unknown save magic {magic!r} - expected PlZ or PlM")
    if len(body) != compressed_len:
        raise SaveError(f"body is {len(body)} bytes, header says {compressed_len}: "
                        f"the file is being written, or is truncated")

    if magic == b"PlZ":
        out = zlib.decompress(body)
        if save_type == DOUBLE:
            out = zlib.decompress(out)
        return out

    try:
        import ooz
    except ImportError as e:  # pragma: no cover - packaging concern, not logic
        raise SaveError(
            "this save uses Oodle compression (PlM) and pyooz is not installed:\n"
            "  pip install -r requirements.txt") from e

    # Oodle needs the output size up front, so the two passes use different lengths:
    # a double-compressed save decompresses first to `compressed_len`, then to
    # `uncompressed_len`. Only the single case is exercised by real saves so far.
    if save_type == DOUBLE:
        return ooz.decompress(ooz.decompress(body, compressed_len), uncompressed_len)
    return ooz.decompress(body, uncompressed_len)


def read_player(path: Path, transform: Transform | None = None) -> PlayerSnapshot:
    """Read one Players/<uid>.sav.

    Deliberately does not touch Level.sav. That file is 1.4MB, needs blob decoders that
    are stale for 1.0.2, and holds nothing this needs - the player's transform is in
    their own save.
    """
    from palworld_save_tools.gvas import GvasFile

    transform = transform or Transform.load()
    gvas = GvasFile.read(decompress(path.read_bytes()))
    try:
        sd = gvas.properties["SaveData"]["value"]
        t = sd["LastTransform"]["value"]["Translation"]["value"]
        uid = str(sd["PlayerUId"]["value"])
        tech = sd.get("UnlockedRecipeTechnologyNames", {}).get("value", {})
    except (KeyError, TypeError) as e:
        # A schema change lands here. Name the missing path: "KeyError: LastTransform"
        # is a fixable report, "save parsing failed" is not.
        raise SaveError(f"save schema not as expected: {e!r}") from e

    return PlayerSnapshot(
        uid=uid,
        map_coords=transform.to_map(t["x"], t["y"]),
        world=(t["x"], t["y"], t["z"]),
        technologies=frozenset(tech.get("values", ())),
        transform_id=transform.transform_id,
        read_at=time.time(),
    )


def newest_player_save(save_dir: Path) -> Path | None:
    """The most recently written player save in a world.

    A world directory can hold several - anyone who has joined a co-op session leaves
    one - and the local player is the one the game is currently writing.
    """
    saves = sorted((save_dir / "Players").glob("*.sav"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return saves[0] if saves else None


class SaveWatcher:
    """Polls the player save and keeps the last good snapshot.

    Polling rather than filesystem events, for two reasons that both come from the game
    rewriting the whole file on autosave: an event fires the instant the write *starts*,
    so half the reads would land on a truncated file, and autosaves are minutes apart
    anyway, so an event-driven design buys nothing but a race.

    `snapshot` therefore lags the player by up to one autosave interval. That is inherent
    to reading a save file rather than the game's memory, and it is acceptable for the
    question being asked: "nearest" is answered against a region, not a footstep.
    """

    def __init__(self, save_dir: Path, interval: float = 20.0):
        self.save_dir = Path(save_dir)
        self.interval = interval
        self.snapshot: PlayerSnapshot | None = None
        self.error: str | None = None
        self._mtime = 0.0
        self._transform = Transform.load()

    def poll(self) -> bool:
        """Re-read if the file changed. True when a new snapshot was taken.

        Every failure is caught and recorded, never raised. This runs on a timer beside
        the bot: an exception here would kill the polling task silently and leave
        "nearest" quietly degraded for the rest of the session.
        """
        path = newest_player_save(self.save_dir)
        if path is None:
            self.error = f"no player save under {self.save_dir / 'Players'}"
            return False

        try:
            mtime = path.stat().st_mtime
        except OSError as e:
            self.error = str(e)
            return False
        if mtime == self._mtime:
            return False

        try:
            self.snapshot = read_player(path, self._transform)
        except SaveError as e:
            # Very likely a read that raced the game's write. Leaving _mtime alone means
            # the next poll retries the same file rather than waiting for another save.
            log.warning("save read failed (%s); keeping previous state", e)
            self.error = str(e)
            return False
        except Exception as e:
            log.exception("unexpected error reading %s", path)
            self.error = f"{type(e).__name__}: {e}"
            return False

        self._mtime = mtime
        self.error = None
        x, y = self.snapshot.map_coords
        log.info("save: player at (%.0f, %.0f), %d technologies",
                 x, y, len(self.snapshot.technologies))
        return True

    def player_coords(self) -> tuple[float, float] | None:
        return self.snapshot.map_coords if self.snapshot else None

    def describe(self) -> str:
        """One line for `/palintel status`."""
        if self.snapshot is None:
            return f"unavailable - {self.error}" if self.error else "not read yet"
        x, y = self.snapshot.map_coords
        age = time.time() - self.snapshot.read_at
        return f"player at ({x:.0f}, {y:.0f}), read {int(age)}s ago"
