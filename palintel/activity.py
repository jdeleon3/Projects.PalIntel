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

import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("palintel.activity")

# Beside the spend ledger and the capture clips, so one session's clips, bill and timings
# sit in one directory and can be read together.
SESSIONS = Path(__file__).resolve().parents[1] / "data" / "sessions"

# One hour of events at a plausible worst case is small; the cap exists so a detector
# stuck firing on a fan cannot grow the deque without bound.
MAX_EVENTS = 2000
DEFAULT_WINDOW = 3600.0

# Stage durations, in milliseconds.
#
# `voice` and `text` cover ANSWERED queries and are what the exit criteria grade
# (p95 <= 2.5s and <= 1.5s over >= 30 real queries each). Declines are timed under
# `voice_decline` / `text_decline` and reported un-graded.
#
# The split is a deliberate criterion decision, not a convenience. A decline costs ~3s
# because the routing policy makes declining the expensive judgement on purpose - it
# names a false decline as the more common failure - and the thinkingLevel sweep showed
# the only way to make it cheaper doubles the wrong-entity rate. Grading both together
# meant the p95 landed on a decline whatever the answer path did, so the bar measured
# the decline rate rather than the speed of an answer. They are different product
# events: "here are the coordinates" and "I can't do that yet" are not the same promise.
#
# Tracked rather than dropped, because a slow decline is still the player waiting, and
# an untracked number is one nobody notices getting worse.
#
# `voice` is measured from end of speech, per the budget in 00-overview.md - not from
# the wake word. The player is not waiting while they are still talking, and starting
# the clock earlier would charge the pipeline for the length of the question.
GRADED_KINDS = ("voice", "text")
DECLINE_KINDS = ("voice_decline", "text_decline")
# Card artwork, deliberately outside the graded total: both happen after the answer is
# already on the channel, so they cost the player nothing they are waiting on. Timed
# anyway, because a reflow arriving long after the card is visible and would otherwise
# go unmeasured - and timed as TWO kinds, because local render and a Discord round trip
# are different costs with different fixes.
ART_KINDS = ("art_render", "art_post")
TIMED_KINDS = (*GRADED_KINDS, *DECLINE_KINDS, "stt", "route", "post", *ART_KINDS)


@dataclass(frozen=True)
class Event:
    at: float
    kind: str          # wake | heard | empty | answered | declined | failed | overflow
    detail: str = ""
    # Milliseconds, for the timing kinds only (voice, text, stt, route, post). None
    # everywhere else - an event that merely happened has no duration, and storing 0
    # would put it in the percentiles as if it were instant.
    ms: float | None = None
    # Who the query belonged to. Empty for stage events that belong to no one speaker.
    who: str = ""


class ActivityLog:
    """Thread-safe ring buffer of pipeline events, optionally persisted.

    Written from the audio thread and the event loop both, so every access takes the
    lock. The work under it is a deque append, which is cheap enough to do in an audio
    callback.

    **The window is one hour and the buffer is memory only, which lost a measurement.**
    The 2026-08-12 session's voice p95 of 6.2s against a 2.5s budget - a Phase 1 exit
    criterion, still recorded as failing - exists nowhere but a status line somebody pasted
    into a chat log. Spend persists because `spend.py` writes a row per query; latency did
    not, so the two halves of "how is this performing" had completely different lifespans.

    A `session` id turns on an append-only JSONL beside the spend ledger, in the same
    session directory, so a run's timings survive it and can be compared with the next.
    """

    def __init__(self, window: float = DEFAULT_WINDOW, session: str | None = None,
                 root: Path | None = None):
        self.window = window
        self.started = time.monotonic()
        self._events: deque[Event] = deque(maxlen=MAX_EVENTS)
        self._lock = threading.Lock()
        self.path: Path | None = None
        if session:
            self.path = (root or SESSIONS) / session / "latency.jsonl"
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def _persist(self, event: Event) -> None:
        """Append one timed event. Never raises: a full disk must not stop the bot.

        Only TIMED events are written. An event that merely happened has no duration, and
        the file exists to answer "how long did it take", not to be a second copy of the
        counters - which are cheap to recompute and worthless after the fact.
        """
        if self.path is None or event.ms is None:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.time(), "kind": event.kind,
                                    "ms": round(event.ms, 1), "who": event.who,
                                    "detail": event.detail[:80]}) + "\n")
        except OSError as e:
            log.warning("could not write latency to %s (%s)", self.path, e)

    def record(self, kind: str, detail: str = "") -> None:
        with self._lock:
            self._events.append(Event(time.monotonic(), kind, detail))

    def timed(self, kind: str, ms: float, detail: str = "", who: str = "") -> None:
        """Record a stage duration. `kind` is one of TIMED_KINDS."""
        event = Event(time.monotonic(), kind, detail, ms, who)
        with self._lock:
            self._events.append(event)
        # Outside the lock: this is file IO, and the audio thread calls `timed`.
        self._persist(event)

    def percentiles(self, kind: str,
                    window: float | None = None) -> tuple[int, float, float] | None:
        """`(n, p50, p95)` in milliseconds, or None when nothing was timed.

        Nearest-rank, not interpolated: with the ~30 samples the exit criterion asks for,
        interpolation invents precision the sample size does not support, and p95 of 30
        is "the second slowest" however it is dressed up. Returning `n` alongside is not
        decoration - a p95 over four queries is not a p95, and the reader has to be able
        to see that.
        """
        xs = sorted(e.ms for e in self.since(window)
                    if e.kind == kind and e.ms is not None)
        if not xs:
            return None
        return len(xs), xs[len(xs) // 2], xs[min(len(xs) - 1, int(len(xs) * 0.95))]

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
