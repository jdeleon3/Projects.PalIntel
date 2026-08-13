"""Short per-user conversation memory — ADR-0013.

Stores **resolved state, not transcripts**. A follow-up resolves against structured facts
already extracted, so the context stays small, the resolution is inspectable, and a wrong
referent is traceable to a specific stored entity rather than to prose the router
re-parsed and re-guessed.

In-process only, never written to disk, consistent with the privacy posture in
01-architecture.md section 9 — the same reason the activity log keeps transcripts in
memory.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

# ADR-0013's defaults. Both are guesses awaiting use: depth bounds how far back a
# referent can reach, and the TTL bounds how stale it can be. A 40-minute-old referent is
# far likelier to be wrong than a 40-second-old one, and the TTL is the cheapest guard
# against the failure this whole module introduces.
MAX_TURNS = 4
TTL_SECONDS = 300.0


@dataclass(frozen=True)
class Turn:
    """One answered query, reduced to what a follow-up could need.

    `entities` holds canonical names only. Storing the raw transcript instead would mean
    re-resolving "Lee's bunk" on every follow-up that referred back to it, with a second
    chance to resolve it differently from the first - the same entity silently becoming
    two different Pals across one conversation.
    """
    who: str
    tool: str | None
    entities: dict[str, str]
    summary: str
    at: float = field(default_factory=time.monotonic)

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else time.monotonic()) - self.at

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.entities.items())
        return f"{self.tool or 'decline'}({args}) -> {self.summary}"


class Memory:
    """Per-user ring buffers of recent turns, TTL-bounded.

    Per user rather than per channel, so two people asking at once do not contaminate
    each other's referents - and spanning input channels, so a question asked by voice can
    be followed up by text.
    """

    # **Reentrant, because `last` and `had_expired` both call `recent`.** A plain Lock
    # would deadlock the first time somebody asked a follow-up. `ActivityLog` uses a plain
    # Lock and can, because nothing there nests.
    #
    # Guarded at all because `recent` MUTATES - it pops expired turns off the left of the
    # deque it is iterating - while `Pipeline.handle` runs in an executor with several
    # workers. Two people asking at once is incidental today and routine under multi-user,
    # which is when this stops being theoretical (Docs/multi-user-design.md section 7).
    def __init__(self, max_turns: int = MAX_TURNS, ttl: float = TTL_SECONDS):
        self.max_turns = max_turns
        self.ttl = ttl
        self._by_user: dict[str, deque[Turn]] = {}
        self._lock = threading.RLock()

    def remember(self, turn: Turn) -> None:
        with self._lock:
            buf = self._by_user.setdefault(turn.who, deque(maxlen=self.max_turns))
            buf.append(turn)

    def recent(self, who: str, now: float | None = None) -> list[Turn]:
        """Live turns for `who`, oldest first. Expired ones are dropped, not returned."""
        with self._lock:
            buf = self._by_user.get(who)
            if not buf:
                return []
            now = now if now is not None else time.monotonic()
            while buf and buf[0].age(now) > self.ttl:
                buf.popleft()
            return list(buf)

    def last(self, who: str, now: float | None = None) -> Turn | None:
        live = self.recent(who, now)
        return live[-1] if live else None

    def had_expired(self, who: str, now: float | None = None) -> bool:
        """True when this user has spoken before but nothing is live any more.

        The distinction the ADR insists on: expired context is not silently ignored. A
        follow-up that reaches back past the TTL has to ask for restatement, because
        answering it against nothing is how "what about the next one" becomes a confident
        card about whatever the router happened to guess.
        """
        with self._lock:
            if not self._by_user.get(who):
                return False
            return not self.recent(who, now)

    def forget(self, who: str | None = None) -> None:
        with self._lock:
            if who is None:
                self._by_user.clear()
            else:
                self._by_user.pop(who, None)

    def describe(self, who: str) -> str:
        live = self.recent(who)
        if not live:
            return "nothing remembered"
        return " | ".join(str(t) for t in live)
