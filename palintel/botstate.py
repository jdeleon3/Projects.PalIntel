"""A heartbeat the bot writes and the console reads.

Two problems with one mechanism, which is why it is a file rather than a socket.

**Is a bot already running?** The console can start the bot as a child process, and a
child outlives its parent: close the console, reopen it, press Start, and you have **two
bots on one Discord token** - both connected, both answering every question, and the only
symptom is duplicate cards. A PID the console remembers does not survive the console, and
a PID on its own is not enough anyway because the OS reuses them.

A heartbeat solves it for any bot however it was started - from this console, from a
terminal, from a scheduled task - because the evidence is a fact about the world rather
than something one process remembers about another.

**What is the bot doing?** Voice state, the Discord receive counters, the router's
identity and uptime exist only in the bot's memory, and the console reported them as
unavailable. They ride along here: the writer already runs on a timer, and a status field
costs nothing beside the file write it is already doing.

Deliberately not a socket or a port. The bot binding a second listener is a new failure
mode on the process whose job is answering questions, and a stale file is a far easier
thing to reason about than a half-open connection.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("palintel.botstate")

REPO = Path(__file__).resolve().parents[1]
STATE_PATH = REPO / "data" / "bot-state.json"

# How often the bot rewrites it, and how old a heartbeat may be before the bot is presumed
# gone. The gap between them is deliberate: a bot briefly blocked - a multi-megabyte save
# parse, a slow Discord round trip - must not read as dead and invite a second one.
BEAT_SECONDS = 5.0
STALE_SECONDS = 20.0


def _path(path: Path | None) -> Path:
    """Resolve the default AT CALL TIME, not at definition time.

    `def read(path=STATE_PATH)` binds the module constant when the function is defined, so
    redirecting `botstate.STATE_PATH` has no effect and nothing that goes through the
    default is testable - which is how the supervisor's "refuse to start a second bot"
    guard, the single most important behaviour here, ended up unable to be exercised.
    """
    return STATE_PATH if path is None else path


def write(status: dict[str, Any], path: Path | None = None) -> None:
    """Stamp the heartbeat. Never raises: a full disk must not stop the bot answering.

    Written to a temp file and replaced, because the console reads this on a timer and a
    half-written file would parse as corrupt exactly when someone is watching to see
    whether the bot is alive.
    """
    path = _path(path)
    payload = {"pid": os.getpid(), "at": time.time(), **status}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        log.debug("could not write %s (%s)", path, e)


def clear(path: Path | None = None) -> None:
    """Remove the heartbeat on a clean shutdown, so the console knows immediately.

    Absence and staleness mean the same thing, so this is a courtesy rather than a
    requirement - which is what lets a killed bot be handled correctly too.
    """
    try:
        _path(path).unlink(missing_ok=True)
    except OSError:
        pass


def read(path: Path | None = None, now: float | None = None) -> dict[str, Any]:
    """What the bot last said about itself, and whether to believe it.

    `running` is the answer to "would starting another one be a mistake". It is false for
    a missing file, an unparseable one, and a stale one - and stale is the interesting
    case, because that is a bot that was killed, crashed, or is wedged badly enough that
    it stopped writing. All three mean the same thing to a Start button.
    """
    path = _path(path)
    now = time.time() if now is None else now
    if not path.exists():
        return {"running": False, "reason": "no heartbeat"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"running": False, "reason": f"unreadable heartbeat ({e})"}

    age = now - float(data.get("at", 0))
    if age > STALE_SECONDS:
        return {"running": False, "reason": f"heartbeat is {age:.0f}s old",
                "stale": True, "age": age, **data}
    return {"running": True, "age": age, **data}
