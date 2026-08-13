"""The heartbeat, and the second-bot problem it exists to prevent.

Two bots on one Discord token both connect and both answer, so the only symptom is every
question arriving twice — and the console is the most likely cause of it: start the bot,
close the console, reopen it, press Start. A handle the console holds does not survive the
console; a heartbeat on disk is a fact about the world and survives everything.
"""
from __future__ import annotations

import json
import time

from palintel import botstate


def test_a_fresh_heartbeat_reads_as_running(tmp_path):
    p = tmp_path / "bot-state.json"
    botstate.write({"router": "gemini"}, p)
    s = botstate.read(p)
    assert s["running"] is True
    assert s["router"] == "gemini"
    assert s["pid"] > 0


def test_no_file_is_not_running(tmp_path):
    s = botstate.read(tmp_path / "absent.json")
    assert s["running"] is False and "no heartbeat" in s["reason"]


def test_a_stale_heartbeat_is_not_running(tmp_path):
    """A bot that was killed, crashed, or is wedged badly enough to stop writing. All
    three mean the same thing to a Start button."""
    p = tmp_path / "bot-state.json"
    p.write_text(json.dumps({"pid": 999, "at": time.time() - 600}), encoding="utf-8")
    s = botstate.read(p)
    assert s["running"] is False and s["stale"] is True
    assert "old" in s["reason"]


def test_a_brief_stall_does_not_read_as_dead(tmp_path):
    """The gap between the beat interval and the staleness bound is deliberate: a bot
    blocked on a multi-megabyte save parse must not invite a second one."""
    p = tmp_path / "bot-state.json"
    p.write_text(json.dumps({"pid": 1, "at": time.time() - botstate.BEAT_SECONDS * 2}),
                 encoding="utf-8")
    assert botstate.read(p)["running"] is True
    assert botstate.STALE_SECONDS > botstate.BEAT_SECONDS * 2


def test_an_unreadable_heartbeat_is_not_fatal(tmp_path):
    p = tmp_path / "bot-state.json"
    p.write_text("{ torn", encoding="utf-8")
    assert botstate.read(p)["running"] is False


def test_a_write_is_atomic_so_a_reader_never_sees_half(tmp_path):
    """Written to a temp file and replaced. The console reads this on a timer, and a
    half-written file would parse as corrupt exactly while someone is watching."""
    p = tmp_path / "bot-state.json"
    botstate.write({"a": 1}, p)
    botstate.write({"a": 2}, p)
    assert json.loads(p.read_text(encoding="utf-8"))["a"] == 2
    assert not p.with_suffix(".tmp").exists()


def test_clear_says_stopped_immediately(tmp_path):
    p = tmp_path / "bot-state.json"
    botstate.write({}, p)
    botstate.clear(p)
    assert botstate.read(p)["running"] is False


def test_writing_never_raises(tmp_path):
    """A full disk must cost the console its status line, never the bot its answers."""
    botstate.write({"x": 1}, tmp_path / "nope" / "deep" / "state.json")  # creates dirs
    botstate.write({"x": object()}, tmp_path / "s.json")                 # unserialisable


# --- the supervisor's refusal -------------------------------------------------

def test_start_refuses_while_a_bot_is_beating(tmp_path, monkeypatch):
    """**The whole point.** The guard is the heartbeat, so it holds against a bot started
    from a terminal, one left by a previous console, and one started by a second console -
    none of which this process has a handle for."""
    from palintel.ui.process import Supervisor

    p = tmp_path / "bot-state.json"
    monkeypatch.setattr(botstate, "STATE_PATH", p)
    botstate.write({"router": "stub"}, p)

    sup = Supervisor()
    res = sup.start()
    assert res["ok"] is False
    assert "already running" in res["error"]
    assert "twice" in res["error"]      # says WHY, not just no


def test_status_marks_a_bot_it_did_not_start_as_adopted(tmp_path, monkeypatch):
    """Adopted bots must remain stoppable. A Start button that refuses because of an
    orphan, with no way to clear the orphan, is worse than no button."""
    from palintel.ui.process import Supervisor

    p = tmp_path / "bot-state.json"
    monkeypatch.setattr(botstate, "STATE_PATH", p)
    botstate.write({"pid": 4242}, p)

    s = Supervisor().status()
    assert s["running"] is True and s["adopted"] is True and s["ours"] is False


def test_stop_reports_when_there_is_nothing_to_stop(tmp_path, monkeypatch):
    from palintel.ui.process import Supervisor

    monkeypatch.setattr(botstate, "STATE_PATH", tmp_path / "absent.json")
    assert Supervisor().stop()["ok"] is False


# --- confirming a stop --------------------------------------------------------

def test_liveness_is_asked_directly_not_inferred_from_the_heartbeat():
    """The bug this replaced: stop polled for the heartbeat to go STALE, but STOP_GRACE
    is deliberately shorter than STALE_SECONDS - a bot briefly blocked must not read as
    dead - so the wait could only ever time out. It reported failure on a process it had
    just successfully killed, which invites you to kill it again."""
    import os

    from palintel.ui import process

    assert process.STOP_GRACE < botstate.STALE_SECONDS      # the trap, pinned
    assert process.alive(os.getpid()) is True
    # A pid that cannot plausibly be running. Not a probe that could terminate anything:
    # os.kill(pid, 0) is a probe on POSIX and a KILL on Windows.
    assert process.alive(2 ** 31 - 1) is False
