"""Wall-clock utterance closure, for sources that stop sending during silence."""
from __future__ import annotations

import pytest

from palintel.listening import SILENCE_MS, State, UtteranceBuffer, _frames

FRAME = b"\x00" * 2560


def capturing() -> UtteranceBuffer:
    b = UtteranceBuffer()
    b.push(FRAME, is_speech=True)
    b.trigger()
    b.push(FRAME, is_speech=True)
    return b


def test_tick_closes_when_packets_stop_arriving():
    """Discord sends nothing during silence, so no frame ever reports the gap."""
    b = capturing()
    later = b._last_frame_at + (SILENCE_MS / 1000) + 0.01
    closed = b.tick(now=later)
    assert closed is not None
    assert closed.reason == "silence"
    assert b.state is State.IDLE


def test_tick_does_not_close_early():
    b = capturing()
    assert b.tick(now=b._last_frame_at + (SILENCE_MS / 1000) / 2) is None
    assert b.state is State.CAPTURING


def test_tick_is_inert_when_not_capturing():
    b = UtteranceBuffer()
    b.push(FRAME, is_speech=False)
    assert b.tick(now=b._last_frame_at + 100) is None


def test_frame_driven_close_still_wins_on_a_mic():
    """A microphone keeps producing frames, so push closes first and tick never fires."""
    b = capturing()
    closed = None
    for _ in range(_frames(SILENCE_MS)):
        closed = b.push(FRAME, is_speech=False) or closed
    assert closed is not None and closed.reason == "silence"
    assert b.tick(now=b._last_frame_at + 100) is None, "must not close twice"


def test_two_questions_separated_by_a_gap_do_not_merge():
    """The observed failure: both questions arrived as one utterance."""
    first = capturing()
    assert first.tick(now=first._last_frame_at + 1.0) is not None
    first.trigger()
    first.push(FRAME, is_speech=True)
    second = first.tick(now=first._last_frame_at + 1.0)
    assert second is not None, "the second question must close on its own"
