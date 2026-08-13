"""M4 — what multi-user costs, and who it costs it for.

Three things that were single-user by omission rather than by decision. Each was already
carrying the field it needed and nothing read it back, which is the shape this repo keeps
finding: measured in isolation, never connected.
"""
from __future__ import annotations

import json

from palintel.activity import ActivityLog
from palintel.capture import SessionCapture, Utterance
from palintel.spend import by_user, describe_users


# --- spend, per person --------------------------------------------------------
#
# `Charge.who` has been written on every row since the ledger existed. Nothing aggregated
# it, so one shared prepaid balance could not answer "who is spending it" - which stops
# being idle the moment a second person can ask.

def _row(who, usd, billed=True):
    return {"who": who, "usd": usd, "billed": billed}


def test_spend_splits_by_person_dearest_first():
    rows = [_row("Rui", 0.01), _row("Friend", 0.05), _row("Rui", 0.02)]
    assert by_user(rows) == [("Friend", 1, 1, 0.05), ("Rui", 2, 2, 0.03)]


def test_an_unattributed_charge_is_named_rather_than_dropped():
    """A row with no `who` is the microphone, or a query from before binding. It must
    still appear in the total or the per-person figures silently stop summing."""
    got = dict((w, n) for w, n, _b, _u in by_user([_row("", 0.01), _row("Rui", 0.01)]))
    assert got == {"(unattributed)": 1, "Rui": 1}


def test_the_share_reaching_the_model_is_kept_not_just_the_money():
    """Usually the more interesting number: it says whose phrasings miss the fast path."""
    rows = [_row("Rui", 0.0, billed=False), _row("Rui", 0.01), _row("Rui", 0.0, False)]
    (who, queries, billed, _usd), = by_user(rows)
    assert (who, queries, billed) == ("Rui", 3, 1)


def test_one_speaker_gets_no_breakdown():
    """A breakdown of one is noise on an already dense card. It appears exactly when it
    starts meaning something."""
    assert describe_users([_row("Rui", 0.01), _row("Rui", 0.02)]) == ""
    assert "Rui" in describe_users([_row("Rui", 0.01), _row("Friend", 0.02)])


def test_a_long_party_is_truncated_rather_than_flooding_the_card():
    rows = [_row(f"p{i}", 0.01 * (10 - i)) for i in range(8)]
    out = describe_users(rows, limit=3)
    assert out.count("|") == 3          # three shown plus the "+N more"
    assert "+5 more" in out


# --- latency, persisted -------------------------------------------------------
#
# The 2026-08-12 voice p95 of 6.2s against a 2.5s budget - a Phase 1 exit criterion still
# recorded as failing - existed only in a status line pasted into a chat log, because the
# log kept a one-hour in-memory window and wrote nothing. Costs persisted; latency did not.

def test_timings_survive_the_process(tmp_path):
    a = ActivityLog(session="s1", root=tmp_path)
    a.timed("voice", 6200.0, "where's the nearest coal", who="Rui")
    rows = [json.loads(x) for x in
            (tmp_path / "s1" / "latency.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["kind"] == "voice"
    assert rows[0]["ms"] == 6200.0
    assert rows[0]["who"] == "Rui"


def test_events_with_no_duration_are_not_written(tmp_path):
    """The file answers "how long did it take". Counters are cheap to recompute and
    worthless after the fact, so writing them would only make it harder to read."""
    a = ActivityLog(session="s2", root=tmp_path)
    a.record("heard", "something")
    a.timed("route", 12.0)
    rows = (tmp_path / "s2" / "latency.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["kind"] == "route"


def test_no_session_means_no_file_and_no_error(tmp_path):
    """The CLI harness and every test construct these without a session."""
    a = ActivityLog()
    a.timed("route", 12.0)
    assert a.path is None
    assert a.percentiles("route") is not None


def test_a_write_failure_does_not_stop_the_bot(tmp_path, monkeypatch):
    """A full disk must cost a measurement, never an answer."""
    a = ActivityLog(session="s3", root=tmp_path)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.open", boom)
    a.timed("voice", 100.0)                     # must not raise
    assert a.percentiles("voice")[0] == 1       # and is still in the window


def test_the_in_memory_window_still_works_alongside_the_file(tmp_path):
    a = ActivityLog(session="s4", root=tmp_path)
    for ms in (100.0, 200.0, 300.0):
        a.timed("voice", ms, who="Rui")
    n, p50, p95 = a.percentiles("voice")
    assert (n, p50) == (3, 200.0)


# --- capture, attributed ------------------------------------------------------

def test_a_captured_clip_records_who_said_it(tmp_path):
    """Two people asking similar questions look exactly like one person rephrasing -
    which is the shape the alias harvester reads as a correction and learns from."""
    cap = SessionCapture(root=tmp_path, session="s5")
    cap.record(Utterance(uid="a", wav="a.wav", seconds=1.0, heard="where's coal",
                         path="fast", tool="find_resource_nodes", entity="coal",
                         score=0.9, outcome="answered", who="Rui"))
    row = json.loads((cap.log_path).read_text(encoding="utf-8").strip())
    assert row["who"] == "Rui"


def test_the_microphone_leaves_who_empty_rather_than_guessing(tmp_path):
    """The mic cannot tell speakers apart. Empty is the honest value; inventing one
    would attribute speech to whoever happens to be configured."""
    cap = SessionCapture(root=tmp_path, session="s6")
    cap.record(Utterance(uid="a", wav="a.wav", seconds=1.0, heard="hi", path="fast",
                         tool=None, entity=None, score=None, outcome="answered"))
    row = json.loads((cap.log_path).read_text(encoding="utf-8").strip())
    assert row["who"] == ""


# --- speaker attribution ------------------------------------------------------
#
# Found by putting the per-user spend split on a screen: four voice sessions on
# 2026-08-13 attributed 20 queries to the literal string `<Object id=366300806208552972>`.
# py-cord 2.8 hands the sink a Member, a User, or a bare Object, and an Object has no
# name - so `str(speaker)` produced a Python repr, which then keyed conversation memory,
# the spend ledger and the capture corpus.

class _Object:
    """py-cord's bare snowflake wrapper: an id and nothing else."""
    def __init__(self, id): self.id = id
    def __str__(self): return f"<Object id={self.id}>"


class _Member:
    def __init__(self, name, id=1): self.display_name, self.id = name, id


def test_a_named_speaker_is_used_as_is():
    from palintel.bot import _speaker_name
    assert _speaker_name(_Member("Ruichan")) == "Ruichan"


def test_an_unresolved_speaker_never_becomes_a_python_repr():
    """The bug itself. `<Object id=...>` is not a name and outlives the session that
    made it - it ends up in the alias harvester's input."""
    got = __import__("palintel.bot", fromlist=["_speaker_name"])._speaker_name(
        _Object(366300806208552972))
    assert got == "speaker 366300806208552972"
    assert "Object" not in got and "<" not in got


def test_a_user_without_a_display_name_falls_back_to_its_name():
    from palintel.bot import _speaker_name

    class _User:
        id, name = 7, "someone"
    assert _speaker_name(_User()) == "someone"
