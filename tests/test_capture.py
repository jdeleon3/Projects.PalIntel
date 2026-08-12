"""Gameplay capture.

The tests that matter are the ones about labels and failure modes, not the happy path.
Capture is diagnostics: it must never damage an answer, and it must never write a label
that reads as truth when it is only what the router believed.
"""
from __future__ import annotations

import json

import pytest

from palintel.capture import FEEDBACK_KINDS, SessionCapture, Utterance, read_session
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


def test_three_feedback_kinds_each_routing_to_a_different_fix():
    """A fourth nobody presses is clutter on every card."""
    assert set(FEEDBACK_KINDS) == {"misheard", "wrong_entity", "wrong_class"}
