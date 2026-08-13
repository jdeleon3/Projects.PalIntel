"""Stale audio must not survive a transmission gap.

Discord stops sending when a speaker stops talking. `_tail` holds the remainder of the
last packet and openWakeWord keeps its own rolling context, so without an explicit reset
the first frame of the next phrase splices minutes-old audio onto "hey pal" - at exactly
the point the model is most sensitive. A microphone cannot produce this, because its
audio never stops.

The channel itself is measured clean (harness/opus_channel_ab.py: 0.957 through a full
Opus round trip against a 0.951 mic baseline), so the gap is the remaining difference
between a file that scores 0.95 and a live session that scatters 0.11-0.97.
"""
from __future__ import annotations

import numpy as np

from palintel.listening import SILENCE_MS
from palintel.voice import SpeakerStream

PACKET = (np.ones(960 * 2, dtype=np.int16) * 500).tobytes()   # one 20ms 48k stereo packet


class FakeWake:
    def __init__(self):
        self.resets = 0
        self.threshold = 0.5

    def push(self, frame):
        return 0.0

    def fired(self, score):
        return False

    def reset(self):
        self.resets += 1


def stream() -> SpeakerStream:
    return SpeakerStream(wake=FakeWake())


def test_gap_clears_the_tail_and_resets_the_detector():
    s = stream()
    s.feed(PACKET)
    assert s._tail.size > 0, "a 20ms packet should leave a remainder under 80ms frames"

    s._last_feed_at -= (SILENCE_MS / 1000) + 0.01
    s.tick()

    assert s._tail.size == 0, "stale samples must not splice onto the next phrase"
    assert s.wake.resets == 1


def test_no_reset_while_audio_is_still_arriving():
    s = stream()
    s.feed(PACKET)
    s.tick()
    assert s.wake.resets == 0, "a mid-phrase tick must not clear the detector"


def test_reset_happens_once_per_gap_not_every_tick():
    s = stream()
    s.feed(PACKET)
    s._last_feed_at -= (SILENCE_MS / 1000) + 0.01
    for _ in range(5):
        s.tick()
    assert s.wake.resets == 1, "repeated ticks during one silence must not re-reset"


def test_speech_after_a_gap_starts_a_fresh_stream():
    s = stream()
    s.feed(PACKET)
    s._last_feed_at -= (SILENCE_MS / 1000) + 0.01
    s.tick()
    s.feed(PACKET)
    assert s._idle is False, "new audio must take the stream out of idle"
    s._last_feed_at -= (SILENCE_MS / 1000) + 0.01
    s.tick()
    assert s.wake.resets == 2, "each gap gets its own reset"
