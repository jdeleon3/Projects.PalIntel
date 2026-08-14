"""What the console can know, and where each fact comes from.

**Everything here is read straight off disk or out of the save**, never out of the bot's
memory. That is not a limitation working around a missing IPC channel - it is what lets
the console be up when the bot is down, which is the case that matters most for a tool
whose jobs include *fixing the config that stopped the bot starting*.

M4 is what made this viable. Before it, latency lived in a one-hour in-memory window and
died with the process; now `latency.jsonl` sits beside `costs.jsonl` and the clips, so a
session's timings, bill and audio can all be read back by anyone.

A short list genuinely is bot-only and is reported as unavailable rather than guessed:
voice/mic state, the Discord receive counters, the router's identity, uptime. Those come
from the bot in a later phase.

**Provenance travels with the number.** Every panel this feeds can say whether a figure was
*measured*, *stated by the game* or *inferred*, because in this project those are different
kinds of claim and the roadmap is a record of confusing them being expensive.
"""
from __future__ import annotations

import json
import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("palintel.ui.sources")

REPO = Path(__file__).resolve().parents[2]
SESSIONS = REPO / "data" / "sessions"


# How a figure came to be known. The console shows this beside anything non-obvious,
# because "measured", "stated" and "inferred" carry different weight here and the project
# has paid for treating them alike.
MEASURED = "measured"      # counted from something this process read
STATED = "stated"          # the game says so
INFERRED = "inferred"      # derived, and could be wrong
UNAVAILABLE = "unavailable"


def _read_jsonl(path: Path) -> list[dict]:
    """Every parseable row. A torn last line from a killed process is skipped, not fatal."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _percentiles(values: list[float]) -> dict[str, float] | None:
    """`n / p50 / p95`, nearest-rank. Matches `activity.percentiles` deliberately.

    Nearest-rank rather than interpolated, for the reason activity.py gives: with the ~30
    samples an exit criterion asks for, interpolation invents a number between two real
    ones and reads as more precise than the sample supports.
    """
    if not values:
        return None
    ordered = sorted(values)
    def rank(p: float) -> float:
        i = max(0, min(len(ordered) - 1, int(round(p * len(ordered) + 0.5)) - 1))
        return ordered[i]
    return {"n": len(ordered), "p50": rank(0.50), "p95": rank(0.95),
            "max": ordered[-1], "mean": statistics.fmean(ordered)}


# --- sessions -----------------------------------------------------------------

@dataclass
class SessionSummary:
    session: str
    started: float
    utterances: int
    answered: int
    declined: int
    fast: int
    model: int
    labelled: int
    usd: float
    clips: int
    has_latency: bool
    has_chat: bool


def _session_started(session: str, rows: list[dict]) -> float:
    """When the session began. The id is a timestamp, and the rows are the fallback."""
    try:
        return time.mktime(time.strptime(session, "%Y%m%d-%H%M%S"))
    except ValueError:
        return min((r.get("at", 0.0) for r in rows if r.get("at")), default=0.0)


def list_sessions(root: Path = SESSIONS) -> list[SessionSummary]:
    """Every capture session on disk, newest first.

    A session directory can hold any subset of clips, `log.jsonl`, `costs.jsonl` and
    `latency.jsonl` - capture and cost are separate flags and either can be off - so every
    count here is independently optional and a session with only a bill is still a session.
    """
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        from ..capture import read_session

        log_path = d / "log.jsonl"
        rows = read_session(log_path) if log_path.exists() else []
        charges = _read_jsonl(d / "costs.jsonl")
        out.append(SessionSummary(
            session=d.name,
            started=_session_started(d.name, rows),
            utterances=len(rows),
            answered=sum(1 for r in rows if r.get("outcome") == "answered"),
            declined=sum(1 for r in rows if r.get("outcome") == "declined"),
            fast=sum(1 for r in rows if r.get("path") == "fast"),
            model=sum(1 for r in rows if r.get("path") == "model"),
            # Only a human verdict counts. `auto` is the router's own opinion of itself,
            # and treating it as a label is how a consistent bug ratifies itself.
            labelled=sum(1 for r in rows if r.get("label") and r["label"] != "auto"),
            usd=sum(c.get("usd", 0.0) for c in charges),
            clips=len(list(d.glob("*.wav"))),
            has_latency=(d / "latency.jsonl").exists(),
            has_chat=(d / "chat.jsonl").exists(),
        ))
    return out


def session_detail(session: str, root: Path = SESSIONS) -> dict[str, Any]:
    """One session, whole: utterances, timings, charges and which clips exist."""
    from ..capture import read_session

    d = root / session
    if not d.is_dir():
        return {}
    rows = read_session(d / "log.jsonl") if (d / "log.jsonl").exists() else []
    clips = {p.stem for p in d.glob("*.wav")}
    for r in rows:
        r["has_clip"] = r.get("uid") in clips

    timings = _read_jsonl(d / "latency.jsonl")
    by_kind: dict[str, list[float]] = {}
    for t in timings:
        if isinstance(t.get("ms"), (int, float)):
            by_kind.setdefault(t.get("kind", "?"), []).append(float(t["ms"]))

    charges = _read_jsonl(d / "costs.jsonl")
    return {
        "session": session,
        "started": _session_started(session, rows),
        "utterances": rows,
        "latency": {k: _percentiles(v) for k, v in sorted(by_kind.items())},
        "charges": {
            "usd": sum(c.get("usd", 0.0) for c in charges),
            "queries": len(charges),
            "billed": sum(1 for c in charges if c.get("billed")),
            "by_user": _by(charges, "who"),
            "by_tool": _by(charges, "tool"),
        },
    }


def _by(charges: list[dict], key: str) -> list[dict]:
    agg: dict[str, dict] = {}
    for c in charges:
        k = c.get(key) or "(none)"
        a = agg.setdefault(k, {"key": k, "queries": 0, "billed": 0, "usd": 0.0})
        a["queries"] += 1
        a["billed"] += bool(c.get("billed"))
        a["usd"] += c.get("usd", 0.0)
    return sorted(agg.values(), key=lambda a: -a["usd"])


def clip_path(session: str, uid: str, root: Path = SESSIONS) -> Path | None:
    """The WAV for one utterance, or None.

    `uid` is checked against the directory listing rather than joined into a path, so a
    crafted uid cannot walk out of the session directory. This binds to loopback, but a
    path traversal in a local server is still a path traversal.
    """
    d = root / session
    if not d.is_dir():
        return None
    for p in d.glob("*.wav"):
        if p.stem == uid:
            return p
    return None


# --- chat (Chat tab, ADR-0018) --------------------------------------------------

# A session id is always `time.strftime("%Y%m%d-%H%M%S")` (`bot.run()` and
# `bot.run_local()` both mint it the same way) - never taken from a request unchecked.
# Every function below that turns a `session` string into a path checks this first, so a
# crafted id cannot walk out of `data/sessions/` the way a bare `root / session` would
# let it. `send_query` below is the one that WRITES a file from this value, which makes
# the check load-bearing rather than defensive dressing.
_SESSION_ID = re.compile(r"^\d{8}-\d{6}$")


def valid_session_id(session: str) -> bool:
    return bool(_SESSION_ID.match(session))


def chat_history(session: str, root: Path | None = None) -> dict[str, Any]:
    """Everything in `chat.jsonl` so far, plus the byte size it was read at.

    The size is not decoration - the Chat tab opens its SSE stream with `?after=<size>`
    so the live tail picks up exactly where this snapshot ends, with nothing replayed
    and nothing missed in between the two requests.

    `root` defaults to the CURRENT value of `sources.SESSIONS`, read at call time rather
    than bound into the signature - the same trap `botstate._path()` was written to
    avoid: a default bound at def time makes redirecting `sources.SESSIONS` (as a test
    does) silently do nothing.
    """
    root = SESSIONS if root is None else root
    if not valid_session_id(session):
        return {"rows": [], "size": 0}
    path = root / session / "chat.jsonl"
    if not path.exists():
        return {"rows": [], "size": 0}
    return {"rows": _read_jsonl(path), "size": path.stat().st_size}


def latest_chat_session(root: Path | None = None) -> str | None:
    """The most recent session with a `chat.jsonl`, for the Chat tab's Read-only state -
    the bot is not running to say which session is "current", so this falls back to the
    same "newest first" ordering `list_sessions` already uses."""
    root = SESSIONS if root is None else root
    if not root.exists():
        return None
    for d in sorted(root.iterdir(), reverse=True):
        if d.is_dir() and valid_session_id(d.name) and (d / "chat.jsonl").exists():
            return d.name
    return None


def send_chat_query(session: str, text: str, root: Path | None = None) -> dict[str, Any]:
    """Write `inbox/<uid>.json` - the console's half of the query-delivery contract
    `bot._handle_inbox_file` reads. The `query` row in `chat.jsonl` is NOT written here:
    that is the bot's job, once it actually claims the file (see `LocalSink.record_query`),
    which is what lets the Chat tab tell "sent" from "the bot saw it" apart at all.
    """
    root = SESSIONS if root is None else root
    if not valid_session_id(session):
        return {"ok": False, "error": "not a valid session id"}
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty message"}
    uid = f"{int(time.time() * 1000):x}"
    inbox = root / session / "inbox"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / f"{uid}.json").write_text(
            json.dumps({"uid": uid, "text": text, "at": time.time()}), encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": f"could not write: {e}"}
    return {"ok": True, "uid": uid}


def art_path(session: str, filename: str, root: Path | None = None) -> Path | None:
    """One artwork file `LocalSink.attach_artwork` wrote under `art/` (step 6, §3.3) -
    `<uid>-image-<index>.jpg` or `<uid>-thumb-<index>.png`, exactly as it appears in the
    `artwork` event's own `images`/`thumbnails` lists, so the Chat tab never has to
    reconstruct a filename itself.

    Checked against the actual directory listing rather than joined blind - same
    discipline `clip_path` already holds itself to, and load-bearing here rather than
    defensive dressing: `filename` comes straight from a request path.
    """
    root = SESSIONS if root is None else root
    if not valid_session_id(session):
        return None
    d = root / session / "art"
    if not d.is_dir():
        return None
    for p in d.iterdir():
        if p.is_file() and p.name == filename:
            return p
    return None


# --- live-ish state, read from the save rather than from the bot ---------------

def save_state(save_dir: Path | None = None) -> dict[str, Any]:
    """World, players, roster and camps - by reading the save exactly as the bot does.

    Deliberately the same code path (`saves.SaveWatcher`), not a reimplementation. Two
    readers of one save that drift apart would make the console lie about the bot in
    precisely the way a console exists to prevent.

    This is a full poll including the multi-megabyte `Level.sav` walk, so it is called on
    request rather than on a timer.
    """
    from ..saves import SaveWatcher

    try:
        w = SaveWatcher(save_dir)
        w.poll()
        w.poll_roster()
    except Exception as e:                       # a save format change must not 500
        log.warning("could not read the save: %s", e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    world = w.world
    age = w.position_age()
    return {
        "ok": True,
        "auto": w.auto,
        "world": None if world is None else {
            "id": world.world_id, "name": world.name, "host": world.host,
            "host_level": world.host_level, "day": world.in_game_day,
            "path": str(world.path), "written_at": world.written_at,
            "provenance": STATED,
        },
        "players": [
            {"uid": uid, "name": name,
             "level": w.level_for(uid),
             "level_provenance": STATED if w.level_for(uid) is not None else UNAVAILABLE,
             "coords": w.coords_for(uid),
             "age": (w.snapshots[uid].age() if uid in w.snapshots else None),
             "technologies": len(w.player_tech(uid).unlocked or ()),
             "points": w.player_tech(uid).points,
             "ancient_points": w.player_tech(uid).ancient_points,
             "roster": (len(w.roster_for(uid)) if w.roster_for(uid) is not None else None)}
            for uid, name in sorted(w.players.items(), key=lambda kv: kv[1] or kv[0])
        ],
        "position_age": age,
        "position_usable": age is None or age <= w.max_position_age,
        "max_position_age": w.max_position_age,
        "roster_world": len(w.roster) if w.roster else None,
        "roster_shared": len(w.rosters.shared) if w.rosters else None,
        "base_camps": w.base_camps,
        "camp_check": None if w.camp_check is None else {
            "located": len(w.camp_check.located),
            "claimed": len(w.camp_check.claimed),
            "agrees": w.camp_check.agrees,
            "describe": w.camp_check.describe(),
        },
        "describe": w.describe(),
    }


def identity_state(world_id: str = "") -> list[dict]:
    """Who has claimed whom, in this world."""
    from ..identity import Bindings

    b = Bindings()
    return [dict(x.as_json()) for x in b._by_user.values()
            if not world_id or x.world == world_id]


def spend_state() -> dict[str, Any]:
    """All-time spend, and the split by person that only matters once there are two."""
    from .. import spend as spend_mod

    rows = spend_mod.all_charges()
    return {
        "usd": sum(r.get("usd", 0.0) for r in rows),
        "queries": len(rows),
        "billed": sum(1 for r in rows if r.get("billed")),
        "by_user": _by(rows, "who"),
        "by_tool": _by(rows, "tool"),
        "provenance": MEASURED,
    }


def latency_state(root: Path = SESSIONS, window_days: float = 30.0) -> dict[str, Any]:
    """Timings across recent sessions, by stage.

    **The budgets are the Phase 1 exit criteria and they are still failing**, so they are
    shown rather than hidden: a console that only reports what passes is decoration.
    """
    cutoff = time.time() - window_days * 86400
    by_kind: dict[str, list[float]] = {}
    for path in sorted(root.glob("*/latency.jsonl")) if root.exists() else []:
        for row in _read_jsonl(path):
            if row.get("at", 0) < cutoff or not isinstance(row.get("ms"), (int, float)):
                continue
            by_kind.setdefault(row.get("kind", "?"), []).append(float(row["ms"]))
    return {
        "kinds": {k: _percentiles(v) for k, v in sorted(by_kind.items())},
        # Docs/00-overview.md section 7. Voice is end to end, text likewise.
        "budgets": {"voice": 2500.0, "text": 1500.0, "stt": 300.0},
        "provenance": MEASURED,
    }
