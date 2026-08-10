"""Utterance buffering and endpointing.

Pure logic, no audio hardware and no models, which is the point: the parts of the voice
path that decide what counts as a query can be tested exhaustively offline, leaving only
the Discord receiver needing a live channel.
"""
from __future__ import annotations

from palintel.listening import (FRAME_SAMPLES, SAMPLE_RATE, State, Utterance,
                                UtteranceBuffer, _frames)

FRAME = b"\x00" * (FRAME_SAMPLES * 2)  # one 80ms frame of 16-bit mono


def _feed(buf: UtteranceBuffer, n: int, speech: bool) -> Utterance | None:
    out = None
    for _ in range(n):
        out = out or buf.push(FRAME, is_speech=speech)
    return out


def test_idle_ignores_audio():
    buf = UtteranceBuffer()
    assert _feed(buf, 50, True) is None
    assert buf.state is State.IDLE


def test_capture_closes_on_trailing_silence():
    buf = UtteranceBuffer(silence_ms=700)
    buf.trigger()
    assert _feed(buf, 10, True) is None          # still talking
    out = _feed(buf, _frames(700), False)
    assert out is not None and out.reason == "silence"
    assert buf.state is State.IDLE               # ready for the next wake word


def test_brief_pauses_do_not_end_the_utterance():
    """People pause mid-sentence. Closing on the first quiet frame would truncate
    "where's the nearest... uh... coal" into "where's the nearest"."""
    buf = UtteranceBuffer(silence_ms=700)
    buf.trigger()
    for _ in range(6):
        assert _feed(buf, 3, False) is None      # ~240ms of quiet, under the threshold
        assert _feed(buf, 3, True) is None
    assert buf.state is State.CAPTURING


def test_hard_cap_closes_rather_than_growing_forever():
    """A noisy room never goes silent; without the cap the buffer never closes."""
    buf = UtteranceBuffer(max_ms=1000)
    buf.trigger()
    out = _feed(buf, _frames(1000) + 5, True)
    assert out is not None and out.reason == "max_length"


def test_pre_roll_is_prepended():
    """The detector fires *after* hearing the phrase, so the first syllables of the
    query are already in the past. Losing them is the "it cut off the start" failure."""
    buf = UtteranceBuffer(pre_roll_ms=400)
    _feed(buf, 20, True)                          # audio before any wake word
    buf.trigger()
    out = _feed(buf, _frames(700), False)
    assert out is not None
    # Pre-roll frames are in the closed buffer even though they preceded the trigger.
    assert out.frames >= _frames(400)


def test_pre_roll_is_bounded():
    """Retaining everything would mean unbounded memory on an idle channel."""
    buf = UtteranceBuffer(pre_roll_ms=200)
    _feed(buf, 500, True)                         # far more than the pre-roll window
    buf.trigger()
    out = _feed(buf, _frames(700), False)
    assert out is not None
    assert out.frames <= _frames(200) + _frames(700) + 2


def test_retrigger_mid_utterance_restarts():
    """"hey pal- hey pal, where's coal" is a correction, not two queries."""
    buf = UtteranceBuffer()
    buf.trigger()
    _feed(buf, 30, True)
    buf.trigger()
    out = _feed(buf, _frames(700), False)
    assert out is not None
    assert out.frames < 30 + _frames(700)         # the first attempt was dropped


def test_utterance_reports_its_duration():
    buf = UtteranceBuffer(silence_ms=700)
    buf.trigger()
    out = _feed(buf, _frames(700), False)
    assert out is not None
    assert abs(out.seconds - out.frames * FRAME_SAMPLES / SAMPLE_RATE) < 1e-9


def test_pcm_is_contiguous_bytes():
    """STT is handed raw PCM; a list of frames would need joining at the call site."""
    buf = UtteranceBuffer(silence_ms=160)
    buf.trigger()
    out = _feed(buf, _frames(160), False)
    assert out is not None
    assert isinstance(out.pcm, bytes)
    assert len(out.pcm) == out.frames * FRAME_SAMPLES * 2


def test_reset_clears_a_partial_capture():
    buf = UtteranceBuffer()
    buf.trigger()
    _feed(buf, 10, True)
    buf.reset()
    assert buf.state is State.IDLE
    assert _feed(buf, 10, True) is None
