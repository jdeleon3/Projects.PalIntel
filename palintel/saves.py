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

**The owned-Pal roster is available, though, and it did not need those decoders fixed.**
`owned_species` reads `Level.sav` with no custom decoders at all and reads `CharacterID`
straight out of the undecoded blob, because the question Q5 asks - *which species do you
have* - is far weaker than the level-and-traits detail the stale decoders exist to
provide. Asking a smaller question turned a decoder-repair project into a property read.
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


# How the save records a defeated tower boss. The value under each key is a bool, and
# only defeated towers were observed present - but absence is read as "not defeated"
# rather than "unknown", which is the conservative direction: it can hold a technology
# back, never offer one that is still locked.
TOWER_FLAG_PREFIX = "BOSS_BATTLE_NAME_"


@dataclass(frozen=True)
class PlayerSnapshot:
    uid: str
    map_coords: tuple[float, float]
    world: tuple[float, float, float]
    technologies: frozenset[str]
    transform_id: str
    read_at: float
    # The two technology-point pools, which are separate currencies and must never be
    # added: `points` buys ordinary technologies and `ancient_points` buys the 51 the
    # table marks `IsBossTechnology`. Both are plain IntProperties in the player save, so
    # unlike the player's level they need no blob decoding at all.
    points: int | None = None
    ancient_points: int | None = None
    # Tower boss suffixes (`ForestBoss`), stripped of TOWER_FLAG_PREFIX so they join
    # straight to the technology table's `EPalBossType::` suffix. **That join is an
    # inference on a key name** - see tech.json's tower_join_note - and it is made here
    # rather than at the point of use so there is one place to correct it.
    towers_defeated: frozenset[str] = frozenset()


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
        # Every field below is optional with `.get`, deliberately. They are read for Q6
        # and nothing else needs them, so a save that stops carrying one must degrade
        # that class rather than take "nearest" down with it - the same posture
        # ADR-0005 takes about the file as a whole.
        points = sd.get("TechnologyPoint", {}).get("value")
        ancient = sd.get("bossTechnologyPoint", {}).get("value")
        record = sd.get("RecordData", {}).get("value", {})
        flags = record.get("TowerBossDefeatFlag", {}).get("value", []) or []
    except (KeyError, TypeError) as e:
        # A schema change lands here. Name the missing path: "KeyError: LastTransform"
        # is a fixable report, "save parsing failed" is not.
        raise SaveError(f"save schema not as expected: {e!r}") from e

    towers = frozenset(
        str(f["key"])[len(TOWER_FLAG_PREFIX):] for f in flags
        if isinstance(f, dict) and f.get("value")
        and str(f.get("key", "")).startswith(TOWER_FLAG_PREFIX))

    return PlayerSnapshot(
        uid=uid,
        map_coords=transform.to_map(t["x"], t["y"]),
        world=(t["x"], t["y"], t["z"]),
        technologies=frozenset(tech.get("values", ())),
        transform_id=transform.transform_id,
        read_at=time.time(),
        points=points,
        ancient_points=ancient,
        towers_defeated=towers,
    )


def newest_player_save(save_dir: Path) -> Path | None:
    """The most recently written player save in a world.

    A world directory can hold several - anyone who has joined a co-op session leaves
    one - and the local player is the one the game is currently writing.
    """
    saves = sorted((save_dir / "Players").glob("*.sav"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return saves[0] if saves else None


def _blob(raw) -> bytes | None:
    """Recover a RawData blob whatever shape the GVAS reader left it in.

    With no custom decoder registered the reader hands back
    `{"values": tuple_of_ints}` - a **tuple**, which an `isinstance(x, list)` check
    misses silently and returns nothing for every entry.
    """
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, (list, tuple)) and raw and isinstance(raw[0], int):
        return bytes(raw)
    if isinstance(raw, dict):
        for key in ("values", "value", "data", "bytes"):
            if key in raw:
                got = _blob(raw[key])
                if got is not None:
                    return got
    return None


def _character_id(blob: bytes) -> str | None:
    """Read `CharacterID` out of an undecoded `PalIndividualCharacterSaveParam`.

    A UE property is `<name><type><size:i64><has_guid:u8><value>`, and the value of a
    NameProperty is a length-prefixed string. **This is parsed rather than pattern
    matched**, because the obvious regex - take the next identifier after
    `CharacterID` - returns the type tag `NameProperty` for every entry in the save.
    That failure is uniform, plausible and total, which is exactly the shape this
    project keeps being bitten by: 554 of 555 "matched" and every one was wrong.
    """
    i = blob.find(b"CharacterID\x00")
    if i < 0:
        return None
    j = blob.find(b"NameProperty\x00", i)
    # The type tag follows the name immediately; anything further away is a different
    # property that merely happens to sit downstream.
    if j < 0 or j - i > 32:
        return None
    p = j + len(b"NameProperty\x00") + 8 + 1
    try:
        (n,) = struct.unpack_from("<i", blob, p)
    except struct.error:
        return None
    if not 0 < n < 128:
        return None
    return blob[p + 4:p + 4 + n - 1].decode("utf-8", "replace") or None


def _world_save_data(level_save: Path) -> dict:
    """`Level.sav`'s worldSaveData, parsed with type hints and NO custom decoders.

    One function because three callers want different parts of the same multi-megabyte
    parse, and because the decoder configuration is the load-bearing part: Phase 0.3
    found at least five `RawData` sub-decoders stale on this build, and disabling all of
    them is what makes the file parse at all.
    """
    from palworld_save_tools.gvas import GvasFile
    from palworld_save_tools.paltypes import PALWORLD_TYPE_HINTS

    gvas = GvasFile.read(decompress(level_save.read_bytes()),
                         PALWORLD_TYPE_HINTS, {}, allow_nan=True)
    return gvas.properties["worldSaveData"]["value"]


def owned_species(level_save: Path) -> frozenset[str]:
    """The set of Pal CharacterIDs the player owns, lower-cased.

    Q5 needs to recommend only Pals you actually have, which needs far less than the
    per-Pal detail behind the stale `RawData` decoders: **species, not level or
    traits.** So this reads `Level.sav` with type hints and *no* custom decoders - the
    configuration Phase 0.3 found parses cleanly - and reads the one field it needs
    out of the raw blob.

    Dropping decoders one at a time does not work: at least five are stale on build
    24467282 (`character`, `map_model`, `foliage_model_instance`, `work`,
    `base_camp`), where the roadmap recorded two.

    **Lower-cased because the two sources disagree about capitalisation.** The save
    writes `Sheepball`; the pak writes `SheepBall`. A case-sensitive join drops that
    Pal with no error at all - it simply goes missing from the owned set, and a
    recommendation quietly omits something you own.

    **Not every entry is a Pal.** Captured humans are in the same map and come back as
    ids like `Believer_Crossbow`, so this is the set of owned *characters*. Callers
    that mean Pals must intersect with the Pal roster rather than trusting the ids -
    otherwise Q5 can offer a captured raider as a counter to a boss.
    """
    entries = _world_save_data(level_save) \
        .get("CharacterSaveParameterMap", {}).get("value", [])

    out, skipped = set(), 0
    for entry in entries:
        blob = _blob(entry.get("value", {}).get("RawData", {}).get("value"))
        name = _character_id(blob) if blob else None
        if name:
            out.add(name.lower())
        else:
            # The player's own character carries no CharacterID, so one skip is
            # expected. A pile of them means the blob layout moved.
            skipped += 1
    log.info("owned species: %d distinct from %d entries (%d without a CharacterID)",
             len(out), len(entries), skipped)
    return frozenset(out)


def _transform_in(blob: bytes) -> tuple[float, float, float] | None:
    """Recover a base camp's world position from an undecoded `BaseCampSaveData` blob.

    The blob holds a serialised `FTransform` — a rotation quaternion (4 doubles), a
    translation (3), then a scale — and 0.24.0 has no decoder for it, the same situation
    `_character_id` is in for `CharacterID`.

    **Found by structure, not by offset.** The preamble varies in length, so this scans
    for the first window where four consecutive doubles form a UNIT QUATERNION and the
    three after them are inside the world's bounds. A fixed offset would be a guess that
    happens to work on three blobs; a unit-length check is a property the data has to
    satisfy, and it is why a wrong window is rejected rather than returned as a
    plausible-looking coordinate somewhere in the sea.
    """
    # Palworld's world is roughly ±1,000,000 world units on each axis. Anything outside
    # that is not a position, it is two doubles that happened to parse.
    limit = 2_000_000.0
    for offset in range(0, max(len(blob) - 56, 0)):
        try:
            quat = struct.unpack_from("<4d", blob, offset)
            trans = struct.unpack_from("<3d", blob, offset + 32)
        except struct.error:
            return None
        if any(abs(v) > 1.0001 for v in quat):
            continue
        if abs(sum(v * v for v in quat) - 1.0) > 1e-6:
            continue
        if any(abs(v) > limit for v in trans) or abs(trans[0]) < 1000:
            continue
        return trans
    return None


def base_camps(level_save: Path,
               transform: Transform | None = None) -> list[tuple[float, float]]:
    """Where the player's base camps are, in map units.

    Read from the same `Level.sav` and the same no-custom-decoders configuration that
    `owned_species` uses, so a caller that wants both pays for one parse. Returns fewer
    entries than the save holds if any blob does not yield a position, which is reported
    rather than raised: a base camp this cannot locate should cost that one camp, not the
    answer.
    """
    transform = transform or Transform.load()
    entries = _world_save_data(level_save).get("BaseCampSaveData", {}).get("value", [])

    out, skipped = [], 0
    for entry in entries:
        blob = _blob(entry.get("value", {}).get("RawData", {}).get("value"))
        found = _transform_in(blob) if blob else None
        if found is None:
            skipped += 1
            continue
        out.append(transform.to_map(found[0], found[1]))
    log.info("base camps: %d located of %d (%d without a readable transform)",
             len(out), len(entries), skipped)
    return out


class SaveWatcher:
    """Polls the player save and keeps the last good snapshot.

    Polling rather than filesystem events, for two reasons that both come from the game
    rewriting the whole file on autosave: an event fires the instant the write *starts*,
    so half the reads would land on a truncated file, and autosaves are minutes apart
    anyway, so an event-driven design buys nothing but a race.

    `snapshot` therefore lags the player by up to one autosave interval. That is inherent
    to reading a save file rather than the game's memory, and it is acceptable for the
    question being asked: "nearest" is answered against a region, not a footstep.

    **The owned-Pal roster is polled separately and much less often**, because it is a
    different order of cost: the player save is a few kilobytes and `Level.sav` is
    megabytes with a full GVAS walk behind it. It has its own interval, its own error
    field and its own last-good value, so a roster read that fails cannot take position
    down with it.

    That roster was built in Phase 3 and **never wired to anything**, which meant every
    counter card in the 2026-08-11 play session said "I haven't read your Pals" while
    `owned_species` sat working and unused. Same class of failure as the counter fast
    path being dark for a day: measured in isolation, never connected. It is polled here
    rather than at query time because a full parse is seconds, not milliseconds.
    """

    # The roster changes when the player catches something, which is minutes apart at
    # best and costs a multi-megabyte parse to observe. Five minutes is far below the
    # rate at which a stale roster could mislead a counter card - the failure it prevents
    # is "you own nothing that works" about a Pal caught an hour ago - and far above the
    # rate at which the parse would cost anything.
    ROSTER_INTERVAL = 300.0

    def __init__(self, save_dir: Path, interval: float = 20.0,
                 roster_interval: float | None = None):
        self.save_dir = Path(save_dir)
        self.interval = interval
        self.roster_interval = (self.ROSTER_INTERVAL if roster_interval is None
                                else roster_interval)
        self.snapshot: PlayerSnapshot | None = None
        self.error: str | None = None
        # None means NOT READ, all the way to the card. See PlayerState.owned_species.
        self.roster: frozenset[str] | None = None
        self.roster_error: str | None = None
        self.roster_read_at: float = 0.0
        # Where the player's base camps are, from the same Level.sav read. None means not
        # read; an empty list means read and they have none.
        self.base_camps: list[tuple[float, float]] | None = None
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

    def poll_roster(self, now: float | None = None) -> bool:
        """Re-read the owned-Pal roster if it is due. True when a new one was taken.

        Failures are recorded and never raised, for the same reason `poll` swallows its
        own: this runs on a timer, and an exception would end the polling task and leave
        both position and roster frozen with nothing on any card to say so.

        A failure keeps the previous roster rather than clearing it. A stale roster is a
        much smaller error than an absent one - it may miss a Pal caught in the last few
        minutes, where None makes every counter card say it never looked.
        """
        now = time.time() if now is None else now
        if now - self.roster_read_at < self.roster_interval:
            return False
        level = self.save_dir / "Level.sav"
        if not level.exists():
            self.roster_error = f"no Level.sav under {self.save_dir}"
            # Stamped even on failure, so a missing file is not re-checked every tick.
            self.roster_read_at = now
            return False
        try:
            self.roster = owned_species(level)
            # Same file, same cadence, and a base camp moves even less often than the
            # roster does. Read here rather than in its own poll so the multi-megabyte
            # parse happens once.
            self.base_camps = base_camps(level, self._transform)
            self.roster_error = None
        except Exception as e:
            log.warning("roster read failed (%s: %s); keeping previous", type(e).__name__, e)
            self.roster_error = f"{type(e).__name__}: {e}"
            self.roster_read_at = now
            return False
        self.roster_read_at = now
        return True

    def player_coords(self) -> tuple[float, float] | None:
        return self.snapshot.map_coords if self.snapshot else None

    def player_tech(self):
        """The Q6 half of the save state, or an empty reading when nothing was read.

        Returns a `progression.PlayerTech`. Imported inside the method because saves.py
        is imported by the config path and progression.py loads a dataset - the same
        reason `counters` is imported inside the dispatcher rather than at module scope.
        """
        from .progression import PlayerTech

        if self.snapshot is None:
            return PlayerTech()
        return PlayerTech(
            unlocked=self.snapshot.technologies,
            points=self.snapshot.points,
            ancient_points=self.snapshot.ancient_points,
            towers_defeated=self.snapshot.towers_defeated,
        )

    def describe_roster(self) -> str:
        """One line for `/palintel status`, beside `describe`."""
        if self.roster is None:
            return f"unavailable - {self.roster_error}" if self.roster_error \
                else "not read yet"
        age = int(time.time() - self.roster_read_at)
        out = f"{len(self.roster)} owned characters, read {age}s ago"
        if self.roster_error:
            # A stale roster is kept rather than cleared, so the line has to say both:
            # the count is real and the last attempt to refresh it did not land.
            out += f" (last refresh failed: {self.roster_error})"
        return out

    def describe(self) -> str:
        """One line for `/palintel status`."""
        if self.snapshot is None:
            return f"unavailable - {self.error}" if self.error else "not read yet"
        x, y = self.snapshot.map_coords
        age = time.time() - self.snapshot.read_at
        return f"player at ({x:.0f}, {y:.0f}), read {int(age)}s ago"
