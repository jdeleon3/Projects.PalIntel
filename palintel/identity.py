"""Who a Discord speaker is in the game — the join multi-user turns on.

The bot serves several people and the save holds several players, and **nothing connects
them**. Palworld does not know about Discord and Discord does not know about Palworld, so
the mapping has to be stated once by each person and then remembered.

**Why this is not a guess.** `newest_player_save()` returns whichever file the game wrote
most recently, which in a co-op world is a different person every few minutes. Answering
an unbound speaker from it is not a fallback, it is cross-attribution: on the two-player
world measured on 2026-08-13, `Rui` has 35 technologies and 83 points while `OutofLuck`
has 61 and 59, so *"what should I research next"* would be answered against the wrong
tree and the wrong budget - on a card with nothing about it that looks wrong. See
[the multi-user design](../Docs/multi-user-design.md) section 6.

**Keyed on the Discord user id, never the display name.** Display names change mid-session
and collide across servers; either one silently splits or merges somebody's identity. The
display name is stored alongside, for showing back to a human who wants to see who is
bound to what.

**Attribute when unambiguous, require binding when not.** A world with one player in it
has one possible answer for everybody, so binding is not required there and single-player
behaviour is untouched. The moment a second player exists the answer stops being
unambiguous and every speaker must say who they are. That is a rule about ambiguity rather
than a special case about player counts, and it degrades in the right direction: adding a
player makes the bot *more* careful, never less.

Persisted, deliberately. A mapping is not player state, so ADR-0005's "PlayerState is held
in memory and never persisted" does not cover it - and rebinding four people after every
restart is how a feature stops being used. It lives in `data/`, which is gitignored,
because it holds Discord user ids.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("palintel.identity")

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO / "data" / "players.json"


@dataclass(frozen=True)
class Binding:
    """One Discord user, claimed to one in-game player, **in one world**.

    `nickname` is the game's own `NickName` at bind time, kept so `/palintel who` can show
    the pairing a human recognises. It is a label: the `uid` is the join, and a player
    renaming themselves in game must not silently unbind them.

    **`world` is not optional and the reason is a collision.** `PlayerUId`
    `00000000-…-0001` is the host in *every* world on this machine - it is the local
    player's id, not a person's - so a binding made in one world matches a different human
    in the next. That was harmless while the save directory was pinned in config and
    became reachable the moment the bot started following whichever world is being played.
    """
    user_id: str
    uid: str
    world: str
    display_name: str = ""
    nickname: str = ""
    at: float = 0.0

    def as_json(self) -> dict:
        return {"user_id": self.user_id, "uid": self.uid, "world": self.world,
                "display_name": self.display_name, "nickname": self.nickname,
                "at": self.at}


class Bindings:
    """Discord user id -> Palworld PlayerUId, loaded and saved as one small JSON file.

    Read on construction and written on every change. There are at most a handful of
    entries and a bind is a deliberate human action, so nothing here needs to be clever
    about IO.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or DEFAULT_PATH
        self._by_user: dict[str, Binding] = {}
        self.load()

    def load(self) -> None:
        """Read the file, or start empty. A corrupt file is reported, never fatal.

        Losing the bindings costs everyone one `/palintel iam` and degrades to
        world-scoped answers in the meantime, which is exactly the honest state. Raising
        here would take the bot down over a file that is a convenience.
        """
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            log.warning("could not read %s (%s); starting with no bindings", self.path, e)
            return
        for row in raw.get("bindings", []):
            try:
                if not row.get("world"):
                    # Written before bindings were world-scoped. Dropped rather than
                    # migrated: there is no world to attribute it to, and guessing one
                    # would recreate the exact collision the scoping exists to prevent.
                    log.warning("dropping a binding with no world: %r", row)
                    continue
                b = Binding(
                    user_id=str(row["user_id"]), uid=str(row["uid"]),
                    world=str(row["world"]),
                    display_name=row.get("display_name", ""),
                    nickname=row.get("nickname", ""), at=float(row.get("at", 0.0)))
                self._by_user[(b.world, b.user_id)] = b
            except (KeyError, TypeError, ValueError):
                log.warning("skipping a malformed binding row: %r", row)
        log.info("identity: %d binding(s) from %s", len(self._by_user), self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"bindings": [b.as_json() for b in self._by_user.values()]}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def bind(self, user_id: str, uid: str, world: str, display_name: str = "",
             nickname: str = "") -> Binding:
        """Claim an in-game player for a Discord user, in one world. Rebinding replaces."""
        b = Binding(user_id=str(user_id), uid=uid, world=world,
                    display_name=display_name, nickname=nickname, at=time.time())
        self._by_user[(world, str(user_id))] = b
        self.save()
        log.info("identity: %s (%s) -> %s (%s) in %s", display_name or user_id, user_id,
                 nickname or "?", uid, world[:8])
        return b

    def unbind(self, user_id: str, world: str) -> bool:
        if (world, str(user_id)) not in self._by_user:
            return False
        self._by_user.pop((world, str(user_id)))
        self.save()
        return True

    def uid_for(self, user_id: str, world: str) -> str | None:
        """The PlayerUId this Discord user claimed **in this world**, or None."""
        b = self._by_user.get((world, str(user_id)))
        return b.uid if b else None

    def binding_for(self, user_id: str, world: str) -> Binding | None:
        return self._by_user.get((world, str(user_id)))

    def user_for(self, uid: str, world: str) -> Binding | None:
        """Who claimed this in-game player, if anyone. For `/palintel who`."""
        return next((b for b in self._by_user.values()
                     if b.uid == uid and b.world == world), None)

    def __len__(self) -> int:
        return len(self._by_user)


def resolve(bindings: "Bindings | None", user_id: str | None,
            known: dict[str, str], world: str = "") -> tuple[str | None, str]:
    """Which in-game player a speaker is, and why.

    Returns `(uid, reason)`. `uid` of None means **do not attribute any player state to
    this speaker** - not "fall back to the host", which is the confidently-wrong answer
    this whole module exists to prevent.

    `known` is uid -> nickname for every player the save holds. Three outcomes:

    - **bound** - they said who they are, and that player is in the save.
    - **the only player** - one player exists, so there is nothing to be ambiguous about.
      Binding is not required and single-player behaviour is unchanged.
    - **unbound** - several players exist and this speaker has not said which. World-scoped
      answers, and the card says so.

    A binding that names a uid the save no longer holds resolves to None rather than to
    something else: a player who left is not the same as a player we can guess at.
    """
    if user_id is not None and bindings is not None:
        uid = bindings.uid_for(user_id, world)
        if uid is not None:
            if uid in known or not known:
                return uid, "bound"
            log.info("identity: %s is bound to %s, which this save does not hold",
                     user_id, uid)
            return None, "bound to a player this save doesn't have"
    if len(known) == 1:
        return next(iter(known)), "the only player in this save"
    if not known:
        return None, "no player saves read"
    return None, "unbound"
