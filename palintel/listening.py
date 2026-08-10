"""Turning a continuous audio stream into discrete utterances.

Sits between the voice receiver and STT, and owns the part of
[ADR-0004](../Docs/adr/0004-wake-word-activation.md) that is pure logic: once a wake word
fires, how much audio is the query, and when does it end.

Three decisions, all of which fail in ways that look like the assistant is broken:

**Pre-roll.** A wake-word detector reports *after* it has heard enough of the phrase, so
by the time it fires the speaker is already saying the query. Buffering only from the
detection loses the first syllables - the classic "it cut off the start of what I said".
`PRE_ROLL_MS` of audio is retained continuously and prepended.

**Endpointing.** The buffer closes on trailing silence rather than a fixed duration,
because "where's coal" and "what's the breeding combo for Astegon and Bellanoir" are not
the same length. ADR-0004's defaults - 700ms of silence, 10s hard cap - are carried over.

**The hard cap.** Without it a room with steady background noise never goes silent and
the buffer grows forever. Hitting the cap is not an error: it closes the buffer and lets
STT have what it got, because a truncated query the router might still answer beats a
silent failure the player cannot diagnose.

Deliberately free of both Discord and openWakeWord: it takes frames in and hands
utterances out, which is what makes it testable against recorded WAVs rather than only
against a live voice channel.
"""
from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from enum import Enum

SAMPLE_RATE = 16_000
# openWakeWord's native frame. Everything upstream is resampled to it, so the buffer
# speaks the same units as the detector and no requantisation is needed mid-stream.
FRAME_SAMPLES = 1280            # 80ms at 16kHz
PRE_ROLL_MS = 500
SILENCE_MS = 700                # ADR-0004 default
MAX_UTTERANCE_MS = 10_000       # ADR-0004 default


def _frames(ms: int) -> int:
    return max(1, round(ms * SAMPLE_RATE / 1000 / FRAME_SAMPLES))


class State(Enum):
    IDLE = "idle"          # listening for a wake word, retaining pre-roll only
    CAPTURING = "capturing"  # wake word fired, accumulating the query


@dataclass
class Utterance:
    """A closed buffer, ready for STT."""
    pcm: bytes
    reason: str            # "silence" | "max_length"
    frames: int
    # `time.monotonic()` at the moment the speaker stopped talking - which is NOT when
    # the buffer closed. A "silence" close happens `silence_ms` after the last speech
    # frame, and the player has been waiting through all of it. The latency budget in
    # 00-overview.md is written from end of speech, so the hangover has to be charged to
    # the answer, and only this class knows how much of it there was.
    ended_at: float = 0.0

    @property
    def seconds(self) -> float:
        return self.frames * FRAME_SAMPLES / SAMPLE_RATE


@dataclass
class UtteranceBuffer:
    """Accumulates frames into one utterance, per speaker.

    One instance per speaker: two people talking at once in a voice channel are two
    independent streams, and mixing them produces audio that transcribes as neither.
    """
    silence_ms: int = SILENCE_MS
    max_ms: int = MAX_UTTERANCE_MS
    pre_roll_ms: int = PRE_ROLL_MS

    state: State = State.IDLE
    _pre: collections.deque = field(init=False, repr=False)
    _buf: list[bytes] = field(default_factory=list, init=False, repr=False)
    _quiet: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self._pre = collections.deque(maxlen=_frames(self.pre_roll_ms))

    def reset(self) -> None:
        self.state = State.IDLE
        self._buf.clear()
        self._quiet = 0

    def trigger(self) -> None:
        """A wake word fired. Start capturing, keeping the pre-roll already heard."""
        if self.state is State.CAPTURING:
            # Re-triggering mid-utterance means the phrase was said again, which is a
            # correction ("hey pal- hey pal, where's coal"). Keep the newer one.
            self._buf.clear()
        self.state = State.CAPTURING
        self._buf.extend(self._pre)
        self._quiet = 0

    def push(self, frame: bytes, is_speech: bool) -> Utterance | None:
        """Feed one frame. Returns an Utterance when the buffer closes."""
        self._pre.append(frame)
        if self.state is not State.CAPTURING:
            return None

        self._buf.append(frame)

        if is_speech:
            self._quiet = 0
        else:
            self._quiet += 1
            if self._quiet >= _frames(self.silence_ms):
                return self._close("silence")

        if len(self._buf) >= _frames(self.max_ms):
            # Not an error. Hand STT what we have: a truncated query the router might
            # still answer beats a silent failure with nothing to diagnose.
            return self._close("max_length")
        return None

    def _close(self, reason: str) -> Utterance:
        pcm, n = b"".join(self._buf), len(self._buf)
        # A max_length close has no hangover to unwind - the buffer filled up and the
        # speaker may well still be talking, so "now" is as close to end of speech as
        # anything available.
        hangover = self.silence_ms / 1000 if reason == "silence" else 0.0
        self.reset()
        return Utterance(pcm=pcm, reason=reason, frames=n,
                         ended_at=time.monotonic() - hangover)
