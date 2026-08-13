"""Gameplay capture — keep the audio the pipeline already wrote, and what it decided.

**The whole point is that the corpus is read speech.** Every recording in
`data/stt_eval/` is a prompt read aloud from a list, and reading is hyperarticulated:
the 68% entity accuracy measured on 2026-08-11 is therefore more likely optimistic than
pessimistic, and every alias harvested that day came from the clearest speech this
speaker produces. Real play is the only source of natural phrasing, game audio bleeding
in, and the truncated utterances already seen twice.

**Capture is free, and that is a fact about the existing code rather than a claim.**
`bot.py` already writes a scratch WAV per utterance, because faster-whisper reads a file
and not a buffer, and then deletes it. Capturing means not deleting. No extra write, no
added latency, and it is already off the audio thread. 16 kHz mono 16-bit is 32 KB/s.

**Audio, not only the transcript.** A transcript is derivable from audio; audio is not
derivable from a transcript. Every experiment run on 2026-08-11 - the model comparison,
the hotword ordering, the alias harvest - re-transcribed existing clips, and none of
them would have been possible from text.

Labels are written as `auto`, which means *the system believed this*, never *this is
true*. That distinction is load-bearing: labels derived from the router's own behaviour
are self-confirming, so a consistent routing bug would be quietly ratified by the corpus
it produces. Only a human, or a rephrase where the retry actually succeeded, upgrades a
label past `auto`.
"""
from __future__ import annotations

import json
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("palintel.capture")

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = REPO / "data" / "sessions"


@dataclass
class Utterance:
    """One captured query. Every field is what the SYSTEM saw, not what was true."""
    uid: str
    wav: str
    seconds: float
    heard: str
    path: str                      # fast | model | decline
    tool: str | None
    entity: str | None
    score: float | None
    outcome: str                   # answered | declined | empty | failed
    message_id: int | None = None  # the join key for retroactive feedback
    label: str = "auto"
    source: str = "gameplay"
    at: float = 0.0

    def as_json(self) -> dict:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["at"] = self.at or time.time()
        return d


class SessionCapture:
    """Writes one session's clips and log. Never raises into the answer path.

    Every method swallows its own errors and logs. Capture is diagnostics: a full disk
    or a permissions problem must degrade the testbed, never the answer the player is
    waiting for. This is the same rule `saves.py` follows for a failed parse.
    """

    def __init__(self, root: Path | None = None, session: str | None = None):
        self.session = session or time.strftime("%Y%m%d-%H%M%S")
        self.dir = (root or DEFAULT_ROOT) / self.session
        self.log_path = self.dir / "log.jsonl"
        self.count = 0
        self._ok = True
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("capture disabled: %s", e)
            self._ok = False

    @property
    def enabled(self) -> bool:
        return self._ok

    def write_wav(self, uid: str, pcm: bytes, rate: int = 16_000) -> Path | None:
        """The clip. Same framing bot.py already uses for the scratch file."""
        if not self._ok:
            return None
        dest = self.dir / f"{uid}.wav"
        try:
            with wave.open(str(dest), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm)
            return dest
        except OSError as e:
            log.warning("capture: could not write %s: %s", dest, e)
            return None

    def record(self, utterance: Utterance) -> None:
        if not self._ok:
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(utterance.as_json(), ensure_ascii=False) + "\n")
            self.count += 1
        except OSError as e:
            log.warning("capture: could not append log: %s", e)

    def attach_message(self, uid: str, message_id: int) -> None:
        """Join a posted card back to the clip that produced it.

        Appended as its own line rather than rewriting the original, because the log is
        opened in append mode and a rewrite would need the whole file held and replaced
        - on the answer path, for a diagnostic. Readers fold these in; `message_id`
        arrives after the answer is posted and nothing upstream waits for it.

        The id is stable across the `art_post` edit, so the join survives artwork being
        attached later.
        """
        if not self._ok:
            return
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"uid": uid, "message_id": message_id}) + "\n")
        except OSError as e:
            log.warning("capture: could not attach message id: %s", e)


    def record_feedback(self, message_id: int, kind: str,
                        who: str | None = None, note: str | None = None) -> None:
        """A human's verdict on a card, keyed by the message it was posted as.

        Outranks `auto` everywhere downstream, and that is the point: labels derived
        from the router's own behaviour are self-confirming, so this is the only channel
        that can contradict it. Written as its own line - the answer is long since
        posted and nothing is waiting.

        `note` is what the player typed. It is an ATTACHMENT to a label and never a
        replacement for one: prose does not aggregate, and `harvest_aliases.py` and the
        scorers consume the label. What it adds is the half no inference can reach - see
        `UNEXPECTED` below.
        """
        if not self._ok:
            return
        row = {"message_id": message_id, "feedback": kind, "label": "user",
               "by": who, "at": time.time()}
        if note:
            row["note"] = note.strip()[:NOTE_LIMIT]
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError as e:
            log.warning("capture: could not record feedback: %s", e)


# Long enough for a sentence about what went wrong, short enough that nobody writes a
# report mid-fight. Discord's own modal maximum is 4000; this is a nudge, not a limit.
NOTE_LIMIT = 500

# The free-text label, and deliberately FIRST in the row.
#
# The other three ask the player to diagnose - was it the microphone, the entity, or the
# class? - which is a router's vocabulary, not a player's. Measured on 2026-08-12: of six
# labels pressed, two were `wrong_class` for things that were not a wrong class at all
# (a technology recommendation that silently dropped "for my mining pals", and a corpus
# answer that restated the question). The taxonomy did not fit and the player used the
# nearest button.
#
# It also reaches what analysis cannot. Replaying that session against the save recovered
# the spend bug, the coordinate parser and the dropped filter - all deterministic. It could
# not recover *"I walked to those coordinates and died"*, which was the most important
# thing the session produced and arrived only because the player said so afterwards.
UNEXPECTED = "unexpected"

# What each button means. The three diagnoses are the ones that route to different fixes -
# a mis-heard name is a lexicon problem, a wrong class is a routing problem, and a wrong
# entity with a clean transcript is neither.
FEEDBACK_KINDS = {
    UNEXPECTED: ("📝", "Not what I expected"),
    "misheard": ("🔇", "Mis-heard me"),
    "wrong_entity": ("❌", "Wrong Pal or item"),
    "wrong_class": ("🤷", "Answered the wrong question"),
}


def read_session(path: Path) -> list[dict]:
    """Fold a session log into one record per utterance.

    `attach_message` appends rather than rewrites, so a uid can appear more than once
    and later lines patch earlier ones. Order is preserved: a session is a sequence, and
    rephrase detection depends on it.
    """
    out: dict[str, dict] = {}
    order: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue        # a torn last line from a killed process, not a reason to fail
        uid = row.get("uid")
        if not uid:
            # Feedback arrives keyed by message id, since a reaction or a button click
            # knows the card and not the clip. Fold it onto whichever utterance claimed
            # that message.
            mid = row.get("message_id")
            if mid is not None:
                for rec in out.values():
                    if rec.get("message_id") == mid:
                        rec.update({k: v for k, v in row.items() if k != "message_id"})
                        break
            continue
        if uid not in out:
            out[uid] = row
            order.append(uid)
        else:
            out[uid].update(row)
    return [out[u] for u in order]


def read_feedback(path: Path) -> list[dict]:
    """Every human verdict in the log, joined or not.

    `read_session` folds feedback onto the utterance that claimed its message and drops
    anything that matches none - which is silent, and fine while every card came from a
    captured clip. It stopped being fine when `/palintel wrong` began accepting a reply to
    a card from the TEXT channel, where there is no clip and therefore no utterance to
    fold onto: the note is the most expensive thing in the file to obtain and the easiest
    to lose.
    """
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "feedback" in row:
            rows.append(row)
    return rows
