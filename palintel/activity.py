"""Recent-activity log, so voice failures stop being invisible.

[ADR-0004](../Docs/adr/0004-wake-word-activation.md) names the risk this exists to
answer: **a wake-word false negative is a silent failure.** The player speaks, nothing
happens, and there is no way to tell a detector that never fired from a mic that is not
capturing, a transcript that came back empty, or a router that declined. All four look
identical from a chair in front of the game.

So each stage records what it saw, and `/palintel status` reports the counts. The value
is in the *shape*: activations with no transcripts means the detector is firing on noise;
no activations at all means the detector or the mic; transcripts with no answers means
routing. Without the breakdown the only available diagnosis is "voice is broken".

Events are held in memory only. This is a diagnostic for the session in progress, not
telemetry - persisting it would mean writing transcripts of everything said near the
microphone to disk, which is exactly what ADR-0004's privacy argument avoids.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

# One hour of events at a plausible worst case is small; the cap exists so a detector
# stuck firing on a fan cannot grow the deque without bound.
MAX_EVENTS = 2000
DEFAULT_WINDOW = 3600.0


@dataclass(frozen=True)
class Event:
    at: float
    kind: str          # wake | heard | empty | answered | declined | failed | overflow
    detail: str = ""


class ActivityLog:
    """Thread-safe ring buffer of pipeline events.

    Written from the audio thread and the event loop both, so every access takes the
    lock. The work under it is a deque append, which is cheap enough to do in an audio
    callback.
    """

    def __init__(self, window: float = DEFAULT_WINDOW):
        self.window = window
        self.started = time.monotonic()
        self._events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._lock = threading.Lock()

    def record(self, kind: str, detail: str = "") -> None:
        with self._lock:
            self._events.append(Event(time.monotonic(), kind, detail))

    def since(self, window: float | None = None) -> list[Event]:
        cutoff = time.monotonic() - (self.window if window is None else window)
        with self._lock:
            return [e for e in self._events if e.at >= cutoff]

    def counts(self, window: float | None = None) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.since(window):
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    def last(self, kind: str) -> Event | None:
        with self._lock:
            for e in reversed(self._events):
                if e.kind == kind:
                    return e
        return None

    def uptime(self) -> float:
        return time.monotonic() - self.started

    def ago(self, kind: str) -> str | None:
        """How long since the last `kind`, already formatted. None if it never happened.

        Formatted here rather than in the caller because `Event.at` is a monotonic clock
        reading - it is meaningless subtracted from anything but another reading of the
        same clock, and handing that number out invites exactly that mistake.
        """
        e = self.last(kind)
        return None if e is None else ago(time.monotonic() - e.at)


def ago(seconds: float) -> str:
    """Coarse on purpose. "14m ago" answers the diagnostic question; "14m 22s" does not."""
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{seconds / 3600:.1f}h ago"


def duration(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    return f"{int(seconds // 3600)}h {int(seconds % 3600 // 60)}m"
