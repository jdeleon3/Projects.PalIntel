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
import os
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

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


def _ago(seconds: float) -> str:
    """A save age a human can read at a glance. `activity.ago` is the same idea for
    events, but this module is imported by the config path and must not pull that in."""
    if seconds < 90:
        return f"{int(seconds)}s"
    if seconds < 5400:
        return f"{int(seconds / 60)}m"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h"
    return f"{int(seconds / 86400)}d"


@dataclass(frozen=True)
class PlayerSnapshot:
    uid: str
    map_coords: tuple[float, float]
    world: tuple[float, float, float]
    technologies: frozenset[str]
    transform_id: str
    # When THIS PROCESS read the file. Says nothing about how old the data is - a save
    # written a fortnight ago and read a second ago has `read_at` of one second, which is
    # the reassuring-and-wrong number `describe()` used to print.
    read_at: float
    # When the GAME wrote the file, from its mtime. The clock that actually matters, and
    # the one nothing consulted until 2026-08-13: without it a coordinate card built from
    # a stale position is byte-for-byte identical to one built from a live one.
    #
    # Survives a network share and a cloud sync, both of which preserve mtime - so this
    # remains the host's real write time even when the save is reached over SMB or synced
    # in from another machine, which is what makes the gate work off-machine too.
    #
    # Defaulted to 0.0 rather than required so a caller constructing a snapshot by hand
    # (the tests do) is not forced to invent one; `age()` reports None for it rather than
    # claiming the save was written in 1970.
    written_at: float = 0.0
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

    def age(self, now: float | None = None) -> float | None:
        """Seconds since the game wrote this save, or None when that is unknown.

        None rather than a large number, so callers must decide what to do about not
        knowing instead of a missing mtime silently reading as "very old" (which would
        turn every hand-built snapshot in the tests into a stale one) or as zero (which
        would be the confident lie this field exists to prevent).
        """
        if not self.written_at:
            return None
        return (time.time() if now is None else now) - self.written_at


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
    # Stat BEFORE the read, so a save rewritten while we were parsing reports the older
    # time and reads as staler than it is. The gate is a safety bound, and it should err
    # toward declining rather than toward vouching for a position it cannot vouch for.
    try:
        written_at = path.stat().st_mtime
    except OSError:
        written_at = 0.0
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
        written_at=written_at,
        points=points,
        ancient_points=ancient,
        towers_defeated=towers,
    )


@dataclass(frozen=True)
class World:
    """One saved world, and enough of its identity to say which one it is out loud.

    **The naming is what makes auto-detection safe rather than reckless.** Picking the
    most recently written world is a heuristic, and a heuristic that picks silently is
    exactly the confidently-wrong shape this project refuses. `LevelMeta.sav` states the
    world's name, its host and the in-game day, so the choice can be shown and a wrong one
    is obvious at a glance instead of presenting as answers about somebody else's
    playthrough.
    """
    path: Path
    world_id: str                 # the directory name, and the key bindings are scoped by
    name: str = ""                # WorldName, e.g. "Explorers Refuge"
    host: str = ""                # HostPlayerName
    host_level: int | None = None
    in_game_day: int | None = None
    written_at: float = 0.0       # Level.sav's mtime

    def describe(self) -> str:
        if not self.name:
            return self.world_id[:8]
        bits = [f"**{self.name}**"]
        if self.host:
            bits.append(f"host {self.host}")
        if self.in_game_day is not None:
            bits.append(f"day {self.in_game_day}")
        return " · ".join(bits)


def save_roots() -> list[Path]:
    """Every Palworld save root on this machine, newest-written first.

    `%LOCALAPPDATA%/Pal/Saved/SaveGames/<steam id>/`. The Steam id folder is enumerated
    rather than configured - there can be more than one if the machine has had more than
    one account, and picking between them is the same problem as picking between worlds,
    so it is left to the caller's newest-wins rule rather than solved twice.
    """
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    root = Path(base) / "Pal" / "Saved" / "SaveGames"
    if not root.is_dir():
        return []
    return [d for d in root.iterdir() if d.is_dir()]


def read_world_meta(world_dir: Path) -> dict:
    """`LevelMeta.sav`'s flat properties, or `{}`.

    2 KB, and it parses with **no custom decoders and no type hints** - unlike `Level.sav`,
    whose stale sub-decoders are the reason half this module reads raw blobs. It states
    `WorldName`, `HostPlayerName`, `HostPlayerLevel` and `InGameDay` outright.
    """
    meta = world_dir / "LevelMeta.sav"
    if not meta.exists():
        return {}
    try:
        from palworld_save_tools.gvas import GvasFile

        gvas = GvasFile.read(decompress(meta.read_bytes()))
        props = gvas.properties
        sd = props.get("SaveData", {}).get("value", props)
        out = {}
        for key, wrapped in (sd.items() if isinstance(sd, dict) else []):
            value = wrapped.get("value") if isinstance(wrapped, dict) else wrapped
            if isinstance(value, (str, int, float, bool)):
                out[key] = value
        return out
    except Exception as e:
        # Never fatal. A world we cannot name is still a world we can read; it just has to
        # be shown by its id, which is worse to look at and no less correct.
        log.info("could not read %s (%s)", meta, e)
        return {}


def find_worlds(roots: list[Path] | None = None) -> list[World]:
    """Every world with a `Level.sav`, most recently written first.

    A directory without one is not a world this bot can read - the 2026-08-02 world on this
    machine is a joined session where the host holds everything, and it contains only
    `LocalData.sav` (see Docs/multi-user-design.md section 4.1.1).
    """
    found: list[World] = []
    for root in (save_roots() if roots is None else roots):
        for world_dir in root.iterdir() if root.is_dir() else []:
            level = world_dir / "Level.sav"
            if not world_dir.is_dir() or not level.exists():
                continue
            try:
                written = level.stat().st_mtime
            except OSError:
                continue
            meta = read_world_meta(world_dir)
            found.append(World(
                path=world_dir, world_id=world_dir.name,
                name=str(meta.get("WorldName", "")),
                host=str(meta.get("HostPlayerName", "")),
                host_level=meta.get("HostPlayerLevel"),
                in_game_day=meta.get("InGameDay"),
                written_at=written))
    return sorted(found, key=lambda w: -w.written_at)


def active_world(roots: list[Path] | None = None) -> World | None:
    """The world most likely being played: the one written most recently.

    **A heuristic, and it is allowed to be one because it is checkable and reversible.**
    The separation on this machine is 0.7 days against 33.5, which is not marginal - and
    where it is wrong the mistake is visible, because the name is shown, and harmless,
    because M0's staleness gate refuses a position from a save nobody has written to
    lately. Setting `game.save_dir` overrides it entirely.
    """
    worlds = find_worlds(roots)
    if not worlds:
        return None
    if len(worlds) > 1:
        log.info("worlds found: %s", ", ".join(
            f"{w.name or w.world_id[:8]} ({_ago(time.time() - w.written_at)} ago)"
            for w in worlds[:4]))
    return worlds[0]


def newest_player_save(save_dir: Path) -> Path | None:
    """The most recently written player save in a world.

    **Not a way to identify a player.** In a co-op world this is a different person every
    few minutes - whoever the game wrote last - so using it to answer an unattributed
    question is cross-attribution, not a fallback. Measured on the two-player world on
    2026-08-13: `Rui` has 35 technologies and `OutofLuck` 61, and "what should I research
    next" answered from this would pick between them by save timing.

    Kept because it is still the right answer to "which file changed most recently", which
    is what `poll` uses it for when deciding whether anything needs re-reading.
    """
    saves = sorted((save_dir / "Players").glob("*.sav"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return saves[0] if saves else None


def player_saves(save_dir: Path) -> list[Path]:
    """Every player save in a world, oldest path order made stable by sorting on name.

    A co-op world holds one per person who has joined - `00000000...0001.sav` for the
    host and a Steam-derived id for everyone else. Each carries that player's own
    position, unlocked technologies, both point pools and tower flags, already parsed by
    `read_player`: the per-player half of multi-user needs no new parsing at all, only
    reading all of these instead of one.
    """
    return sorted((save_dir / "Players").glob("*.sav"), key=lambda p: p.name)


def _str_property(blob: bytes, name: bytes) -> str | None:
    """Read a StrProperty out of an undecoded blob, UTF-8 or UTF-16.

    Same parse-don't-pattern-match discipline as `_character_id`, and the same reason: the
    obvious regex returns the type tag. A negative length prefix means UTF-16, which is
    how the game stores any nickname with a non-ASCII character in it.
    """
    i = blob.find(name + b"\x00")
    if i < 0:
        return None
    j = blob.find(b"StrProperty\x00", i)
    if j < 0 or j - i > 32:
        return None
    p = j + len(b"StrProperty\x00") + 8 + 1
    try:
        (n,) = struct.unpack_from("<i", blob, p)
    except struct.error:
        return None
    if n > 0:
        if not 0 < n < 256:
            return None
        return blob[p + 4:p + 4 + n - 1].decode("utf-8", "replace") or None
    n = -n
    if not 0 < n < 256:
        return None
    return blob[p + 4:p + 4 + (n - 1) * 2].decode("utf-16-le", "replace") or None


def player_names(level_save: Path) -> dict[str, str]:
    """PlayerUId -> the name that player chose in game, for every player in the world.

    This is what lets identity binding be `/palintel iam Rui` rather than a Guid nobody
    can type or verify. Read from the same no-custom-decoders `Level.sav` parse the roster
    uses, so a caller wanting both pays for one walk.

    A human player's entry is the one carrying `IsPlayer`; its map key holds the same
    `PlayerUId` that names their file in `Players/`, which is what makes the join exact
    rather than inferred. Measured on the 2026-08-13 co-op world: `Rui` and `OutofLuck`,
    both joining their save files.
    """
    entries = _world_save_data(level_save) \
        .get("CharacterSaveParameterMap", {}).get("value", [])

    out: dict[str, str] = {}
    for entry in entries:
        blob = _blob(entry.get("value", {}).get("RawData", {}).get("value"))
        if not blob or b"IsPlayer\x00" not in blob:
            continue
        uid = str(entry.get("key", {}).get("PlayerUId", {}).get("value") or "")
        if not uid:
            continue
        # A player with no readable nickname still counts as a player - they can be bound
        # by uid - so the entry is kept with an empty name rather than dropped.
        out[uid] = _str_property(blob, b"NickName") or ""
    log.info("players in this world: %s",
             ", ".join(f"{n or '?'} ({u[:8]})" for u, n in out.items()) or "none found")
    return out


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


def _le_guid(raw: bytes) -> str:
    """A UE `FGuid` - four little-endian uint32 - as a canonical UUID string.

    The byte order is the whole trick and it is easy to get silently wrong: the player
    save writes `PlayerUId` as the string `00000000-...-0001` while the same value inside
    a blob is `00000000 00000000 00000000 01000000`. Reading it as big-endian produces a
    valid-looking Guid that joins to nothing, which is this project's favourite kind of
    bug.
    """
    a, b, c, d = struct.unpack("<4I", raw[:16])
    return str(UUID(f"{a:08x}{b:08x}{c:08x}{d:08x}"))


NULL_GUID = "00000000-0000-0000-0000-000000000000"

# A container slot's `RawData`: PlayerUId(16) + InstanceId(16) + 6 trailing bytes.
# Established by reading real slots on 2026-08-13 rather than from documentation - see
# Docs/multi-user-design.md section 2.6, where the first guess at a layout was wrong.
_SLOT_MIN = 32


def _slot_instance(raw: bytes) -> str | None:
    """The character an occupied slot holds, or None for an empty slot."""
    if len(raw) < _SLOT_MIN:
        return None
    inst = _le_guid(raw[16:32])
    return None if inst == NULL_GUID else inst


@dataclass(frozen=True)
class Rosters:
    """Which Pals belong to whom — the M2 answer, and it is two sets rather than one.

    **The naive filter is wrong and it was measured.** `OwnerPlayerUId` is present on 516
    of 559 characters in the reference save and joins exactly, so filtering on it looks
    like the whole answer. It drops the roster from **195 species to 184** on a save with
    exactly one player in it: the 43 without an owner all carry `SlotIndex`, meaning they
    sit in a base-camp container, and eleven species exist *only* there - six of them
    ordinary Pals `counters.py` would shortlist. A perfect, well-formed, silent 5.6% loss.

    So the scope is `carried | shared`, which is what a guild MEANS: base Pals are usable
    by any member. That has a property worth having - on a single-player world it returns
    exactly the 195 the union always did, so this change is behaviour-preserving where it
    can be checked against a number that already exists.

    Measured 2026-08-13: solo 184 carried + 36 shared = 195. Co-op `Rui` 32 carried and
    `OutofLuck` 39, 19 in common, 8 shared species - never the 53 the union reported to
    both of them.
    """
    # uid -> species that player carries, in their own Palbox and party.
    carried: dict[str, frozenset[str]]
    # Species in containers no player save claims: base camps, which the guild shares.
    shared: frozenset[str]
    # Every species in the world. What `owned_species` returned to everybody, kept so a
    # caller with no attribution can still say something true about the world.
    everyone: frozenset[str]

    def for_player(self, uid: str | None) -> frozenset[str] | None:
        """What this player can field, or None when we cannot attribute at all.

        None is not "owns nothing" - it stays None all the way to the card, which says it
        has not looked rather than claiming an empty roster. See `PlayerState.owned_species`.
        """
        if uid is None:
            return None
        if uid not in self.carried:
            return None
        return self.carried[uid] | self.shared


def _player_container_ids(player_save: Path) -> list[str]:
    """The Palbox and party container ids a player's own save names.

    **Stated, not inferred.** `PalStorageContainerId` and `OtomoCharacterContainerId` are
    plain properties in `Players/<uid>.sav`, so "which containers are mine" needs no
    guessing - which matters, because the guild's own handle list is keyed by the map-key
    PlayerUId and splits 239/1 rather than by ownership (design section 2.6).
    """
    from palworld_save_tools.gvas import GvasFile

    gvas = GvasFile.read(decompress(player_save.read_bytes()))
    sd = gvas.properties["SaveData"]["value"]
    out = []
    for key in ("PalStorageContainerId", "OtomoCharacterContainerId"):
        cid = sd.get(key, {}).get("value", {}).get("ID", {}).get("value")
        if cid:
            out.append(str(cid))
    return out


def read_rosters(save_dir: Path) -> Rosters:
    """Who owns which Pals, in one `Level.sav` walk plus the player saves.

    The join, each step of which the save states:

        Players/<uid>.sav  names PalStorageContainerId and OtomoCharacterContainerId
          -> CharacterContainerSaveData[id].Slots
            -> slot RawData carries an InstanceId
              -> CharacterSaveParameterMap keyed by InstanceId -> CharacterID

    A container that no player save claims is a base camp's, and its Pals are the guild's.
    Verified complete on both reference worlds: every character with a species is reached
    this way (558 of 558, and 238 of 238), so nothing is silently dropped by the join.
    """
    wsd = _world_save_data(save_dir / "Level.sav")

    species_of: dict[str, str] = {}
    for entry in wsd.get("CharacterSaveParameterMap", {}).get("value", []):
        inst = str(entry.get("key", {}).get("InstanceId", {}).get("value") or "")
        blob = _blob(entry.get("value", {}).get("RawData", {}).get("value"))
        name = _character_id(blob) if blob else None
        # Lower-cased for the same reason `owned_species` is: the save writes `Sheepball`
        # and the pak writes `SheepBall`, and a case-sensitive join drops that Pal with no
        # error at all.
        if inst and name:
            species_of[inst] = name.lower()

    contents: dict[str, set[str]] = {}
    for c in wsd.get("CharacterContainerSaveData", {}).get("value", []):
        cid = str(c.get("key", {}).get("ID", {}).get("value") or "")
        if not cid:
            continue
        found = set()
        for slot in c.get("value", {}).get("Slots", {}).get("value", {}).get("values", []):
            raw = _blob(slot.get("RawData", {}).get("value"))
            inst = _slot_instance(raw) if raw else None
            if inst:
                found.add(inst)
        contents[cid] = found

    carried: dict[str, frozenset[str]] = {}
    claimed: set[str] = set()
    for path in player_saves(save_dir):
        try:
            uid = read_player(path).uid
            ids = _player_container_ids(path)
        except Exception as e:
            # One unreadable player save must not cost everyone else their roster.
            log.warning("could not read containers from %s (%s)", path.name, e)
            continue
        claimed.update(ids)
        insts = set().union(*(contents.get(i, set()) for i in ids)) if ids else set()
        carried[uid] = frozenset(species_of[i] for i in insts if i in species_of)

    shared_insts: set[str] = set()
    for cid, insts in contents.items():
        if cid not in claimed:
            shared_insts |= insts
    shared = frozenset(species_of[i] for i in shared_insts if i in species_of)

    everyone = frozenset(species_of.values())
    log.info("rosters: %d player(s), shared %d species, %d in the world",
             len(carried), len(shared), len(everyone))
    for uid, sp in sorted(carried.items()):
        log.info("  %s carries %d species (+%d shared)", uid[:8], len(sp), len(shared))
    return Rosters(carried=carried, shared=shared, everyone=everyone)


class _Cursor:
    """A forward reader over an undecoded blob, bounds-checked at every step.

    Raises rather than returning nonsense, so a layout that has moved fails loudly here
    instead of producing plausible garbage further up. Everything that calls this catches
    and falls back.
    """

    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def _need(self, n: int) -> None:
        if self.p + n > len(self.b):
            raise SaveError(f"blob ends at {len(self.b)}, wanted {n} at {self.p}")

    def guid(self) -> str:
        self._need(16)
        out = _le_guid(self.b[self.p:self.p + 16])
        self.p += 16
        return out

    def i32(self) -> int:
        self._need(4)
        (v,) = struct.unpack_from("<i", self.b, self.p)
        self.p += 4
        return v

    def u8(self) -> int:
        self._need(1)
        v = self.b[self.p]
        self.p += 1
        return v

    def skip(self, n: int) -> None:
        self._need(n)
        self.p += n

    def string(self) -> str:
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            self._need(n)
            out = self.b[self.p:self.p + n - 1].decode("utf-8", "replace")
            self.p += n
            return out
        n = -n
        self._need(n * 2)
        out = self.b[self.p:self.p + (n - 1) * 2].decode("utf-16-le", "replace")
        self.p += n * 2
        return out


# A plausibility bound on any count read out of the guild blob. A wrong offset reads a
# chunk of a Guid as a length and produces something enormous; refusing early turns that
# into a clean failure rather than a multi-gigabyte allocation.
_MAX_COUNT = 1_000_000


def guild_base_ids(level_save: Path) -> dict[str, list[str]]:
    """Which base camps each guild claims: group id -> camp ids.

    **A second opinion on `base_camps`, which is why this exists.** Base camp positions are
    recovered by scanning an undecoded blob for a unit quaternion followed by an in-bounds
    translation (`_transform_in`) - a structural check rather than a fixed offset, but
    still a scan, and STATUS lists it as uncalibrated at "3 of 3 on one save". The guild
    states its camps outright, so the two can be compared: agreement is evidence, and
    disagreement means one of them is wrong and should be said out loud.

    Same standard `build_bosses.py` holds two sources to - they agree on everything they
    share, and the build fails if they stop.

    **Fail-closed.** The layout was established by locating known values on two real saves
    (Docs/multi-user-design.md), and one `i32` between `org_type` and the base-id count is
    zero on both and otherwise unexplained. Anything unexpected - that field non-zero, an
    implausible count, a short buffer - returns nothing rather than a guess, because the
    only thing this is FOR is checking another parse, and a check that can be wrong is
    worse than no check.
    """
    wsd = _world_save_data(level_save)
    known = {str(c.get("key")) for c in wsd.get("BaseCampSaveData", {}).get("value", [])}

    out: dict[str, list[str]] = {}
    for group in wsd.get("GroupSaveDataMap", {}).get("value", []):
        value = group.get("value", {})
        if "Guild" not in str(value.get("GroupType", {}).get("value")):
            continue
        raw = _blob(value.get("RawData", {}).get("value"))
        if not raw:
            continue
        try:
            c = _Cursor(raw)
            group_id = c.guid()
            c.string()                       # group_name: the host's uid as a string
            handles = c.i32()
            if not 0 <= handles <= _MAX_COUNT:
                raise SaveError(f"implausible handle count {handles}")
            # Each handle is a (PlayerUId, InstanceId) pair. Skipped rather than read:
            # this list is keyed by the map-key uid and splits 239/1 on the co-op save,
            # so it says nothing about ownership - see the design's section 2.6.
            c.skip(handles * 32)
            c.u8()                           # org_type
            unexplained = c.i32()
            if unexplained != 0:
                # Zero on both reference saves and not understood. Non-zero means the
                # layout is not what was established, and every offset after it is a
                # guess. Refuse.
                raise SaveError(f"unexpected field {unexplained} before the base ids")
            count = c.i32()
            if not 0 <= count <= _MAX_COUNT:
                raise SaveError(f"implausible base count {count}")
            ids = [c.guid() for _ in range(count)]
        except (SaveError, struct.error) as e:
            log.warning("guild blob did not parse as expected (%s); no cross-check", e)
            continue

        # The self-validating step, and the reason this can be trusted at all: a camp id
        # the camp table does not hold means the walk landed somewhere else and produced
        # sixteen bytes that merely look like a Guid.
        stray = [i for i in ids if i not in known]
        if stray:
            log.warning("guild %s claims %d camp(s) the save does not hold (%s); "
                        "discarding the cross-check", group_id, len(stray), stray[:2])
            continue
        out[group_id] = ids
    return out


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
    return [xy for _, xy in located_base_camps(level_save, transform)]


def located_base_camps(level_save: Path,
                       transform: Transform | None = None
                       ) -> list[tuple[str, tuple[float, float]]]:
    """Base camps as `(camp_id, (x, y))`, so a position can be named rather than counted.

    The id is `BaseCampSaveData`'s own key, which is what `guild_base_ids` returns - so
    the two can be compared camp by camp instead of only by total. A count check would
    pass if the scan found a phantom and missed a real one.
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
        out.append((str(entry.get("key")),
                    transform.to_map(found[0], found[1])))
    log.info("base camps: %d located of %d (%d without a readable transform)",
             len(out), len(entries), skipped)
    return out


@dataclass(frozen=True)
class CampCheck:
    """Whether the two accounts of where the base camps are agree.

    `located` is what the quaternion scan recovered; `claimed` is what the guilds say they
    own. `missing` is the interesting one - a camp the guild claims and the scan could not
    place, which is the failure mode the scan's own log line has always reported and which
    nothing has ever surfaced.
    """
    located: frozenset[str]
    claimed: frozenset[str]

    @property
    def missing(self) -> frozenset[str]:
        """Claimed by a guild, not located. The scan fell short."""
        return self.claimed - self.located

    @property
    def unclaimed(self) -> frozenset[str]:
        """Located, but no guild claims it. Not necessarily wrong - a camp can outlive
        the guild that built it - so this is reported and never acted on."""
        return self.located - self.claimed

    @property
    def agrees(self) -> bool:
        return not self.missing

    def describe(self) -> str:
        if not self.claimed:
            return f"{len(self.located)} located, no guild claim to check against"
        if self.agrees and not self.unclaimed:
            return f"{len(self.located)} located, all claimed by a guild"
        parts = [f"{len(self.located)} located of {len(self.claimed)} claimed"]
        if self.missing:
            parts.append(f"**{len(self.missing)} not found by the scan**")
        if self.unclaimed:
            parts.append(f"{len(self.unclaimed)} claimed by nobody")
        return ", ".join(parts)


def check_base_camps(level_save: Path,
                     transform: Transform | None = None) -> CampCheck:
    """Compare the scanned camp positions against what the guilds claim to own."""
    located = {cid for cid, _ in located_base_camps(level_save, transform)}
    claimed: set[str] = set()
    for ids in guild_base_ids(level_save).values():
        claimed.update(ids)
    check = CampCheck(frozenset(located), frozenset(claimed))
    if not check.agrees:
        # Loud, because it means the scan silently returned fewer camps than exist and
        # "rate my base" has been answering about a subset with nothing to say so.
        log.warning("base camp cross-check: %s", check.describe())
    else:
        log.info("base camp cross-check: %s", check.describe())
    return check


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

    # How old the save may be before its POSITION stops being offered - 15 minutes.
    #
    # **This is a bound, not a calibration, and it is deliberately generous.** Nobody has
    # recorded Palworld's autosave cadence during play; the backup directories on this
    # machine hold snapshots ten minutes and an hour apart, which brackets it and does not
    # pin it. So the number is chosen to be comfortably longer than any plausible autosave
    # interval - the failure it must not cause is refusing "nearest" to a player who is
    # sitting right there between saves - while still being far shorter than the case it
    # exists for: the bot running with the game closed, or a save reached over a share or
    # a cloud sync that has silently stopped updating.
    #
    # A real measurement replaces this: one session's worth of mtimes settles it, and the
    # gate should be tightened to something like three autosave intervals once there is a
    # number to multiply.
    #
    # **Position only.** The roster, the technology set and the base camps are all
    # slow-moving - you catch a Pal every few minutes at best, research less often than
    # that, and move a base almost never - so a save that is an hour old still carries a
    # roster worth filtering a counter card by. Gating them on this bound would throw away
    # good answers to prevent an error they cannot make. Position is the one field where
    # being minutes out of date changes the answer, and the one that sends someone
    # somewhere.
    MAX_POSITION_AGE = 900.0

    def __init__(self, save_dir: Path | None = None, interval: float = 20.0,
                 roster_interval: float | None = None,
                 max_position_age: float | None = None):
        # `None` means follow whichever world is being played. A stated path pins one and
        # is never second-guessed: someone who names a directory means that directory.
        self.auto = save_dir is None
        self.world: World | None = None
        if self.auto:
            self.world = active_world()
            save_dir = self.world.path if self.world else Path()
            if self.world is not None:
                log.info("auto-detected world: %s", self.world.describe())
        self.save_dir = Path(save_dir)
        self.interval = interval
        self.roster_interval = (self.ROSTER_INTERVAL if roster_interval is None
                                else roster_interval)
        self.max_position_age = (self.MAX_POSITION_AGE if max_position_age is None
                                 else max_position_age)
        # Every player in the world, by PlayerUId. A co-op save holds one entry per
        # person; a single-player save holds exactly one. Filled by `poll`.
        self.snapshots: dict[str, PlayerSnapshot] = {}
        # uid -> the name that player chose in game, from Level.sav on the roster cadence.
        # What `/palintel who` shows and what `/palintel iam <name>` matches against.
        self.players: dict[str, str] = {}
        # The one unambiguous snapshot: set only when the world holds exactly ONE player,
        # where there is nothing to be ambiguous about. None with two or more, which is
        # what forces every speaker to say who they are rather than being answered from
        # whichever save the game happened to write last.
        self.snapshot: PlayerSnapshot | None = None
        self.error: str | None = None
        # None means NOT READ, all the way to the card. See PlayerState.owned_species.
        # The WORLD's roster - every species anyone owns. Kept because it is what a
        # caller with no attribution can still say something true about, and because
        # `describe_roster` reports on the world rather than on one player.
        self.roster: frozenset[str] | None = None
        # Per-player rosters and the guild's shared base Pals. None means not read.
        self.rosters: "Rosters | None" = None
        # Whether the scanned base camp positions agree with what the guilds claim.
        # None means the check has not run.
        self.camp_check: "CampCheck | None" = None
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
        # Following the game between worlds. Re-checked on the ordinary poll because it is
        # a handful of stats, and because a player who quits to menu and loads another
        # world would otherwise keep getting answers about the one they left - which reads
        # as the bot being stale rather than as it watching the wrong place.
        if self.auto and self._follow_active_world():
            return False        # everything was just discarded; read it on the next tick

        paths = player_saves(self.save_dir)
        if not paths:
            self.error = f"no player save under {self.save_dir / 'Players'}"
            return False

        # The newest mtime across ALL of them, so one player moving is enough to trigger
        # a re-read. Watching only the newest FILE would work by accident here; watching
        # the newest TIME is what the change check actually means.
        try:
            mtime = max(p.stat().st_mtime for p in paths)
        except OSError as e:
            self.error = str(e)
            return False
        if mtime == self._mtime:
            return False

        # **Every player, not the newest one.** Each file is a few kilobytes and holds its
        # own position, technologies and both point pools, so reading all of them costs
        # almost nothing and is the whole of the per-player half of multi-user. Reading
        # only the newest is what made a co-op world answer everybody as whoever the game
        # wrote last.
        found: dict[str, PlayerSnapshot] = {}
        failures: list[str] = []
        for path in paths:
            try:
                snap = read_player(path, self._transform)
            except SaveError as e:
                # Very likely a read that raced the game's write. One player's torn save
                # must not cost the others theirs, so this is per-file rather than fatal.
                failures.append(f"{path.name}: {e}")
                continue
            except Exception as e:
                log.exception("unexpected error reading %s", path)
                failures.append(f"{path.name}: {type(e).__name__}: {e}")
                continue
            found[snap.uid] = snap

        if not found:
            # Nothing readable. Leaving _mtime alone means the next poll retries rather
            # than waiting for another autosave.
            log.warning("no player save could be read (%s); keeping previous state",
                        "; ".join(failures))
            self.error = "; ".join(failures)
            return False

        # A player whose file failed this time keeps their previous snapshot, for the same
        # reason the roster does: stale is a far smaller error than absent, which would
        # make their cards say we never looked.
        self.snapshots.update(found)
        self._mtime = mtime
        self.error = "; ".join(failures) if failures else None
        # Unambiguous only when the world holds exactly one player. See `identity.resolve`:
        # this is a rule about ambiguity, not a special case about counts.
        self.snapshot = (next(iter(self.snapshots.values()))
                         if len(self.snapshots) == 1 else None)
        for uid, snap in sorted(found.items()):
            x, y = snap.map_coords
            log.info("save: %s at (%.0f, %.0f), %d technologies, %s/%s points",
                     self.players.get(uid) or uid[:8], x, y, len(snap.technologies),
                     snap.points, snap.ancient_points)
        return True

    def _follow_active_world(self) -> bool:
        """Switch to the newest world if it changed. True when it did.

        **Everything is discarded on a switch, not merged.** Positions, rosters, player
        names, base camps and the camp check all describe the world we just left, and a
        roster from the wrong playthrough is the same confidently-wrong card as a roster
        from the wrong player. Even the `PlayerUId`s collide - `00000000-…-0001` is the
        host in *every* world - so keeping anything would silently re-attribute it.
        """
        found = active_world()
        if found is None or (self.world is not None
                             and found.world_id == self.world.world_id):
            return False
        log.info("world changed: %s -> %s",
                 self.world.describe() if self.world else "(none)", found.describe())
        self.world = found
        self.save_dir = found.path
        self.snapshots, self.snapshot = {}, None
        self.players, self.rosters, self.roster = {}, None, None
        self.base_camps, self.camp_check = None, None
        self._mtime, self.roster_read_at = 0.0, 0.0
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
            # Who is in this world, by name. Same file, same cadence, same walk as the
            # roster - and it changes even less often, since a player joins once.
            # Read first so a roster failure still leaves the names available for
            # `/palintel who`, which is what someone reaches for when binding fails.
            self.players = player_names(level)
        except Exception as e:
            log.warning("player names read failed (%s: %s)", type(e).__name__, e)
        try:
            # Per player, plus the guild's shared base Pals. `everyone` is what
            # `owned_species` used to return to all comers, so the world-scoped answer
            # survives for callers with no attribution.
            self.rosters = read_rosters(self.save_dir)
            self.roster = self.rosters.everyone
            # Same file, same cadence, and a base camp moves even less often than the
            # roster does. Read here rather than in its own poll so the multi-megabyte
            # parse happens once.
            self.base_camps = base_camps(level, self._transform)
            # A second opinion on the scan that produced those positions. Cheap - it
            # re-reads nothing, since `_world_save_data` is the same walk - and it is the
            # only check this project has on a parse STATUS lists as uncalibrated.
            self.camp_check = check_base_camps(level, self._transform)
            self.roster_error = None
        except Exception as e:
            log.warning("roster read failed (%s: %s); keeping previous", type(e).__name__, e)
            self.roster_error = f"{type(e).__name__}: {e}"
            self.roster_read_at = now
            return False
        self.roster_read_at = now
        return True

    def player_coords(self) -> tuple[float, float] | None:
        """Where the player was at the last autosave, or None if that is too old to say.

        **The gate that was missing until 2026-08-13.** Nothing checked the save's age, so
        with the game closed the bot answered "where's the nearest coal" against whatever
        position the file last held - a week ago, a month ago - and `describe()` reported
        "read 3s ago", which is the wrong clock and reads as reassurance.

        A coordinate card built from a stale position is byte-for-byte identical to one
        built from a live position. There is no tell. And this is the class that sends the
        player somewhere: the 2026-08-12 session produced a card that got them killed by
        naming a real place, so "confidently wrong about where you are" is not a
        hypothetical failure mode here.

        None is the honest answer and every card already handles it - `find_resource_nodes`
        ranks by cluster size instead of distance, the spawn card drops its `Nearest:` row,
        and "rate this spot" declines and asks for a coordinate. The honest path existed
        the whole time; nothing was wired to it.
        """
        return self.coords_for(None)

    def coords_for(self, uid: str | None) -> tuple[float, float] | None:
        """`player_coords` for one named player, or the unambiguous one when uid is None.

        An unknown uid returns None rather than falling back. A speaker we cannot place is
        answered about the world, never about somebody else - see `identity.resolve`.
        """
        snap = self.snapshot_for(uid)
        if snap is None:
            return None
        age = snap.age()
        if age is not None and age > self.max_position_age:
            # Logged rather than silent: "nearest stopped working" needs to be answerable
            # without a debugger, and the answer here is usually "the game is not running".
            log.info("save is %.0fs old (> %.0fs): not offering a position",
                     age, self.max_position_age)
            return None
        return snap.map_coords

    def snapshot_for(self, uid: str | None) -> "PlayerSnapshot | None":
        """One player's snapshot. `None` uid means the unambiguous single player."""
        if uid is None:
            return self.snapshot
        return self.snapshots.get(uid)

    def position_age(self, uid: str | None = None) -> float | None:
        """Seconds since the game wrote the position, or None if unknown or unread."""
        snap = self.snapshot_for(uid)
        return snap.age() if snap else None

    def state_for(self, uid: str | None):
        """The `PlayerState` for one speaker. **The whole point of M1.**

        A `uid` of None is not an error and not a fallback: it is a speaker we could not
        place, and they get a state with every field absent. Every card already handles
        that - resource lookup ranks by cluster size and says so, counters say they have
        not read your Pals, Q6 declines outright rather than recommending tier-1 research
        to a level-57 player. What must never happen is answering them from another
        player's save, which is the confidently-wrong card multi-user introduces.

        The roster is per player as of M2: what they carry, plus what their guild shares
        in its base camps. Base camps themselves are still world-scoped - that is M3.
        """
        from .pipeline import PlayerState

        if uid is None:
            return PlayerState()
        return PlayerState(
            player_coords=self.coords_for(uid),
            owned_species=self.roster_for(uid),
            tech=self.player_tech(uid),
            base_camps=self.base_camps,
        )

    def roster_for(self, uid: str | None) -> frozenset[str] | None:
        """One player's Pals - carried plus their guild's shared base Pals.

        Falls back to the WORLD roster only when the per-player read has not happened yet
        and there is exactly one player, which is the single-player case where the two are
        the same set by construction. With two players it returns None rather than the
        union, because handing `Rui` all 53 species when 21 of them are `OutofLuck`'s is
        the over-count this phase exists to remove.
        """
        if self.rosters is not None:
            found = self.rosters.for_player(uid)
            if found is not None:
                return found
            # A uid the roster read does not know: a player who joined since the last
            # roster poll. None rather than the union - "I haven't looked" beats a count
            # that includes somebody else's Pals.
            return None
        if uid is not None and len(self.snapshots) <= 1:
            return self.roster
        return None

    def player_tech(self, uid: str | None = None):
        """The Q6 half of the save state, or an empty reading when nothing was read.

        Returns a `progression.PlayerTech`. Imported inside the method because saves.py
        is imported by the config path and progression.py loads a dataset - the same
        reason `counters` is imported inside the dispatcher rather than at module scope.

        **Per player, and this is where multi-user bites hardest.** Technology is the most
        divergent thing in a co-op save: on the 2026-08-13 world `Rui` has 35 unlocked and
        83/7 points against `OutofLuck`'s 61 and 59/8. `PlayerTech()` with `unlocked=None`
        means never read, and `progression_card` declines on it rather than recommending
        from an empty set - so an unplaceable speaker gets an honest card, not a shopping
        list built from somebody else's tree.
        """
        from .progression import PlayerTech

        snap = self.snapshot_for(uid)
        if snap is None:
            return PlayerTech()
        return PlayerTech(
            unlocked=snap.technologies,
            points=snap.points,
            ancient_points=snap.ancient_points,
            towers_defeated=snap.towers_defeated,
        )

    def describe_roster(self) -> str:
        """One line for `/palintel status`, beside `describe`."""
        if self.roster is None:
            return f"unavailable - {self.roster_error}" if self.roster_error \
                else "not read yet"
        age = int(time.time() - self.roster_read_at)
        if self.rosters is not None and len(self.rosters.carried) > 1:
            # A co-op world. Reporting one total would be the over-count M2 removes -
            # every player would read it as theirs.
            who = ", ".join(
                f"{self.players.get(u) or u[:8]} {len(sp | self.rosters.shared)}"
                for u, sp in sorted(self.rosters.carried.items()))
            return (f"{who} (incl. {len(self.rosters.shared)} shared), "
                    f"{len(self.roster)} in the world, read {age}s ago")
        out = f"{len(self.roster)} owned characters, read {age}s ago"
        if self.camp_check is not None and not self.camp_check.agrees:
            # Only when it disagrees. A passing cross-check is not news, and a status card
            # that reports every check that passed buries the one that did not.
            out += f" | base camps: {self.camp_check.describe()}"
        if self.roster_error:
            # A stale roster is kept rather than cleared, so the line has to say both:
            # the count is real and the last attempt to refresh it did not land.
            out += f" (last refresh failed: {self.roster_error})"
        return out

    def describe(self) -> str:
        """One line for `/palintel status`.

        **Reports how old the SAVE is, not how recently we read it.** The old line said
        "read 3s ago" about a file the game wrote a fortnight earlier, which is true,
        useless, and actively reassuring in the one situation where the status card is
        being consulted because something looks wrong.
        """
        # Which world, first, and only when nobody named one. An auto-detected pick is a
        # heuristic and must be visible; a configured path is what the reader already knows.
        lead = (f"{self.world.describe()} — " if self.auto and self.world else "")
        if len(self.snapshots) > 1:
            return lead + self._describe_players()
        if self.snapshot is None:
            return lead + (f"unavailable - {self.error}" if self.error
                           else "not read yet")
        return lead + self._describe_one()

    def _describe_players(self) -> str:
        """A co-op world. There is no single "the player", so naming one would be the
        cross-attribution M1 exists to stop - the line lists them instead."""
        who = ", ".join(
            f"{self.players.get(u) or u[:8]} ({_ago(s.age())} ago)"
            if s.age() is not None else (self.players.get(u) or u[:8])
            for u, s in sorted(self.snapshots.items()))
        return f"{len(self.snapshots)} players: {who}"

    def _describe_one(self) -> str:
        x, y = self.snapshot.map_coords
        age = self.snapshot.age()
        if age is None:
            # No mtime. Fall back to the read clock and say which one it is, rather than
            # printing a number whose meaning the reader has to guess.
            return (f"player at ({x:.0f}, {y:.0f}), "
                    f"read {int(time.time() - self.snapshot.read_at)}s ago "
                    f"(save age unknown)")
        line = f"player at ({x:.0f}, {y:.0f}), saved {_ago(age)} ago"
        if age > self.max_position_age:
            # The position is not being used, so the status card must not imply it is.
            line += " - **too old to use, ranking by cluster size**"
        return line
