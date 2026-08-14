"""Gameplay capture.

The tests that matter are the ones about labels and failure modes, not the happy path.
Capture is diagnostics: it must never damage an answer, and it must never write a label
that reads as truth when it is only what the router believed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from palintel.capture import (FEEDBACK_KINDS, UNEXPECTED, SessionCapture, Utterance,
                              read_feedback, read_session)
from palintel.config import CaptureConfig


def cap(tmp_path) -> SessionCapture:
    return SessionCapture(root=tmp_path, session="s")


def utt(uid="a", **kw) -> Utterance:
    base = dict(uid=uid, wav=f"{uid}.wav", seconds=1.0, heard="how do I beat Anubis",
                path="fast", tool="plan_counters", entity="Anubis", score=0.99,
                outcome="answered")
    return Utterance(**{**base, **kw})


def test_both_flags_default_off():
    """Capture records whatever is near the mic. That is a decision, not a default."""
    c = CaptureConfig()
    assert c.enabled is False and c.feedback is False


def test_a_recorded_label_is_auto_not_truth():
    """Labels from the router's own behaviour are self-confirming; saying so in the
    data is what stops a consistent bug being ratified by its own corpus."""
    assert utt().as_json()["label"] == "auto"
    assert utt().as_json()["source"] == "gameplay"


def test_a_wav_and_a_log_line_round_trip(tmp_path):
    c = cap(tmp_path)
    assert c.write_wav("a", b"\x00\x01" * 800) is not None
    c.record(utt())
    rows = read_session(c.log_path)
    assert len(rows) == 1 and rows[0]["entity"] == "Anubis"


def test_the_message_id_joins_a_card_back_to_its_clip(tmp_path):
    """Appended, not rewritten - the id does not exist until Discord assigns it."""
    c = cap(tmp_path)
    c.record(utt())
    c.attach_message("a", 12345)
    rows = read_session(c.log_path)
    assert len(rows) == 1 and rows[0]["message_id"] == 12345


def test_feedback_lands_on_the_right_utterance_by_message_id(tmp_path):
    """Not "the last utterance", which breaks as soon as two more questions follow."""
    c = cap(tmp_path)
    c.record(utt("a")); c.attach_message("a", 111)
    c.record(utt("b")); c.attach_message("b", 222)
    c.record_feedback(111, "misheard", who="Ruichan")
    rows = {r["uid"]: r for r in read_session(c.log_path)}
    assert rows["a"]["feedback"] == "misheard" and rows["a"]["label"] == "user"
    assert "feedback" not in rows["b"] and rows["b"]["label"] == "auto"


def test_human_feedback_outranks_the_auto_label(tmp_path):
    c = cap(tmp_path)
    c.record(utt("a")); c.attach_message("a", 1)
    c.record_feedback(1, "wrong_entity")
    assert read_session(c.log_path)[0]["label"] == "user"


def test_order_is_preserved_because_rephrase_detection_needs_it(tmp_path):
    c = cap(tmp_path)
    for uid in ("a", "b", "c"):
        c.record(utt(uid))
    assert [r["uid"] for r in read_session(c.log_path)] == ["a", "b", "c"]


def test_a_torn_last_line_does_not_lose_the_session(tmp_path):
    """A killed process leaves a partial line. Losing one clip beats losing the log."""
    c = cap(tmp_path)
    c.record(utt("a"))
    with c.log_path.open("a", encoding="utf-8") as f:
        f.write('{"uid": "b", "hea')
    assert [r["uid"] for r in read_session(c.log_path)] == ["a"]


def test_an_unwritable_directory_disables_rather_than_raises(tmp_path):
    """Capture is diagnostics. A full disk must degrade the testbed, never the answer."""
    blocker = tmp_path / "f"
    blocker.write_text("not a directory", encoding="utf-8")
    c = SessionCapture(root=blocker, session="s")
    assert not c.enabled
    c.record(utt())            # must not raise
    c.record_feedback(1, "misheard")
    assert c.write_wav("a", b"\x00") is None


def test_the_feedback_kinds_lead_with_free_text():
    """Three diagnoses, each routing to a different fix, plus the one that asks instead.

    The diagnoses are a ROUTER's vocabulary. Play on 2026-08-12 pressed `wrong_class`
    twice for things that were not a wrong class - the nearest available button - which is
    what earned `unexpected` its slot at the head of the row rather than a fourth
    diagnosis nobody presses.
    """
    assert set(FEEDBACK_KINDS) == {UNEXPECTED, "misheard", "wrong_entity", "wrong_class"}
    assert next(iter(FEEDBACK_KINDS)) == UNEXPECTED


def test_a_note_rides_with_the_label_and_never_replaces_it(tmp_path: Path):
    """Prose does not aggregate and the scorers consume the label, so a note is an
    attachment. It must not be possible to record one without a kind."""
    c = SessionCapture(root=tmp_path, session="s")
    c.record_feedback(7, UNEXPECTED, who="jd", note="  sent me somewhere I died  ")
    rows = [json.loads(line) for line in
            (c.dir / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["feedback"] == UNEXPECTED
    assert rows[-1]["label"] == "user"
    assert rows[-1]["note"] == "sent me somewhere I died"


def test_a_note_survives_a_card_no_clip_claims(tmp_path: Path):
    """`/palintel wrong` accepts a reply to a card answered from the text channel, where
    there is no utterance to fold onto. `read_session` drops those; `read_feedback` is
    what stops the most expensive row in the file being lost silently."""
    c = SessionCapture(root=tmp_path, session="s")
    c.record_feedback(999, UNEXPECTED, note="typed query, no clip")
    assert read_session(c.dir / "log.jsonl") == []
    assert [r["note"] for r in read_feedback(c.dir / "log.jsonl")] \
        == ["typed query, no clip"]


# --- near / unrecognized, added 2026-08-14 for the item free-text resolver ------------

def test_near_and_unrecognized_default_to_none():
    """A row from a router that never names either must not fabricate one - `None` is
    the honest reading, and most routers still leave both unset."""
    d = utt(near=None, unrecognized=None).as_json()
    assert d["near"] is None and d["unrecognized"] is None


def test_near_and_unrecognized_round_trip(tmp_path):
    """Additive fields: an old reader that has never heard of `near` still gets every
    other column unchanged, which is the whole point of adding a field rather than
    repurposing `entity`."""
    c = cap(tmp_path)
    c.record(utt(entity=None, near="Giga Glider", unrecognized="gildra",
                 outcome="declined"))
    row = read_session(c.log_path)[0]
    assert row["entity"] is None
    assert row["near"] == "Giga Glider"
    assert row["unrecognized"] == "gildra"


def test_answer_populates_near_and_unrecognized():
    """Read off the source, the same technique `test_text_capture.py` uses for the
    capture/feedback wiring defect: the risk is an ABSENT keyword, which a call that
    only ever exercises the answered path would not catch either."""
    import ast

    bot = Path(__file__).resolve().parents[1] / "palintel" / "bot.py"
    tree = ast.parse(bot.read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "_answer":
            for call in ast.walk(node):
                if isinstance(call, ast.Call) \
                        and getattr(call.func, "id", None) == "Utterance":
                    calls.append(call)
    assert calls, "no Utterance( call inside _answer - has it been renamed?"
    for call in calls:
        kw = {k.arg for k in call.keywords if k.arg}
        assert "near" in kw, "top candidate is not being captured on a decline"
        assert "unrecognized" in kw, "the router's own named culprit is not captured"
