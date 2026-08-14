"""Output sinks — where an answered query's cards actually go.

`bot._answer` computes an `Outcome` once, medium-agnostically: routing, capture, spend
and activity timing are the same regardless of where the cards end up. Delivery is the
one part that is not, and everything Discord-specific that used to live inline in
`bot.py` — embed rendering, attachments, the feedback buttons — moved here with it, so
`bot.py` depends on this module and never the other way around. See
[`Docs/local-output-design.md`](../Docs/local-output-design.md) and
[ADR-0018](../Docs/adr/0018-local-output-medium.md) for why this boundary exists at all.

**Two phases, not one**, because Discord's own latency budget forces it: the graded
promise is "here are the coordinates", and a map crop is not part of it, so text goes out
on its own round trip and artwork attaches after the clock has already stopped
(`Docs/00-overview.md` §7). Both phases sit on `OutputSink` even though a local sink has
no such latency reason to split them — `DiscordSink`'s behaviour is a pure extraction of
what `bot._answer` used to do inline, not a redesign.
"""
from __future__ import annotations

import io
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .capture import FEEDBACK_KINDS, NOTE_LIMIT, UNEXPECTED, SessionCapture
from .cards import Card

log = logging.getLogger("palintel.sinks")

try:
    import discord
except ImportError:  # pragma: no cover
    discord = None


# --------------------------------------------------------------- rendering (shared)

def to_embed(card: Card, index: int = 0, *, with_art: bool = True) -> "discord.Embed":
    embed = discord.Embed(title=card.title,
                          description="\n".join(card.lines),
                          color=card.colour)
    if card.footer:
        embed.set_footer(text=card.footer)
    if with_art:
        names = card.attachments(index)
        if "image" in names:
            embed.set_image(url=f"attachment://{names['image']}")
        if "thumbnail" in names:
            embed.set_thumbnail(url=f"attachment://{names['thumbnail']}")
    return embed


def art_files(cards_: list[Card]) -> list["discord.File"]:
    """The attachments the embeds reference, in the order their cards appear."""
    files = []
    for i, card in enumerate(cards_):
        names = card.attachments(i)
        if card.image is not None:
            files.append(discord.File(io.BytesIO(card.image), filename=names["image"]))
        if card.thumbnail is not None:
            files.append(discord.File(str(card.thumbnail), filename=names["thumbnail"]))
    return files


# --------------------------------------------------------------- feedback (Discord only)

class FeedbackView(discord.ui.View):
    """Labelling buttons under an answer card.

    **Components, not reactions.** A button row rides in the same `send()` payload at no
    extra API cost, where six reactions are six REST calls that would have to be deferred
    behind the answer like `art_post` is. The card is posted once, with its controls.

    Four buttons. Three are diagnoses that each route to a different fix - a mis-heard
    name is a lexicon problem, a wrong class is a routing problem, and a wrong entity on
    a clean transcript is neither. The fourth asks for a sentence instead of a diagnosis,
    and leads the row; see `capture.UNEXPECTED` for why it earned the slot.

    `timeout=None` keeps them live for the session. They do NOT survive a bot restart -
    persistent views need registering at startup, and this is a testbed control rather
    than a promise to the player. **That expiry is why `/palintel wrong` exists**: the
    two defects this session found were both "I acted on the card and the world
    disagreed", knowable only after travelling, by which time the buttons are long out of
    view. The message-id join was always retroactive; only the buttons were not.

    Local mode has no equivalent yet - the console's Chat tab is read-only history when
    the bot is stopped and live text when it is not, and neither state has renderable
    Discord components. A local feedback path is its own design question, not answered
    here.
    """

    def __init__(self, capture: SessionCapture):
        super().__init__(timeout=None)
        for kind, (emoji, label) in FEEDBACK_KINDS.items():
            self.add_item(_FeedbackButton(capture, kind, emoji, label))


class _NoteModal(discord.ui.Modal):
    """One optional field, asking what was expected instead.

    A modal rather than a follow-up message because it costs nothing until it is opened:
    the button rides in the card's own `send()` payload, and this is created only on a
    click. Nothing is required, so a mis-click is one Escape away and never a half-written
    row in the log.

    **py-cord, not discord.py**, and the two differ here in ways that fail at click time
    rather than at import: the submit hook is `callback`, not `on_submit`; the field is
    `InputText`; and the style enum is `InputTextStyle`, since `discord.TextStyle` does
    not exist in this library at all. Written with py-cord's own names so the mismatch is
    visible in the source rather than discovered by a player pressing a button.
    """

    def __init__(self, capture: SessionCapture, message_id: int):
        super().__init__(title="What did you expect?")
        self._capture, self._message_id = capture, message_id
        self._note = discord.ui.InputText(
            label="What went wrong?",
            placeholder="e.g. sent me to a level 70 area I can't survive",
            style=discord.InputTextStyle.paragraph,
            max_length=NOTE_LIMIT,
            required=False)
        self.add_item(self._note)

    async def callback(self, interaction: discord.Interaction) -> None:
        note = self._note.value or ""
        self._capture.record_feedback(
            self._message_id, UNEXPECTED,
            who=getattr(interaction.user, "display_name", None),
            note=note)
        log.info("feedback %s on message %s: %r", UNEXPECTED, self._message_id, note[:80])
        await interaction.response.send_message("Noted - thanks.", ephemeral=True)


class _FeedbackButton(discord.ui.Button):
    def __init__(self, capture: SessionCapture, kind: str, emoji: str, label: str):
        super().__init__(
            # The free-text one is the primary action and looks like it.
            style=(discord.ButtonStyle.primary if kind == UNEXPECTED
                   else discord.ButtonStyle.secondary),
            emoji=emoji, label=label)
        self._capture, self._kind = capture, kind

    async def callback(self, interaction: discord.Interaction) -> None:
        # Keyed by the MESSAGE, not by "the last utterance" - which breaks the moment two
        # more questions follow, and this is meant to be usable minutes later.
        if self._kind == UNEXPECTED:
            # A modal IS the response to the interaction, so nothing is written until the
            # player submits - and `send_message` must not be called before it.
            await interaction.response.send_modal(
                _NoteModal(self._capture, interaction.message.id))
            return
        self._capture.record_feedback(
            interaction.message.id, self._kind,
            who=getattr(interaction.user, "display_name", None))
        log.info("feedback %s on message %s", self._kind, interaction.message.id)
        # Ephemeral: the acknowledgement is for the person who clicked, and a public
        # "noted" under every corrected card is noise for everyone else.
        await interaction.response.send_message("Noted - thanks.", ephemeral=True)


# --------------------------------------------------------------------- the Sink contract

@dataclass(frozen=True)
class Posted:
    """A handle to a delivered answer, opaque to the caller beyond this.

    `message_id` is the capture join key ONLY when a sink needs a separate one —
    Discord's snowflake, assigned after the send, because the join key does not exist
    until Discord has assigned it. A sink whose own record is already keyed by the
    query's `uid` returns `None` here: there is nothing to join, because there was never
    a second identifier to reconcile.
    """
    message_id: int | None = None


class OutputSink(Protocol):
    """Where an answer goes, in two phases: text now, artwork once it exists - plus one
    progress signal in between, for a medium that can show it.
    """

    async def stage(self, uid: str | None, stage: str) -> None:
        """A live progress marker - today just `"routing_started"`, fired right before
        the router call, the one thing worth showing "in progress" (see ADR-0018's note
        on why this is not token streaming). A no-op for a medium with nothing to show
        it on; `DiscordSink`'s is exactly that, so this call costs every existing caller
        nothing.
        """
        ...

    async def post(self, cards: list[Card], *, feedback: bool) -> Posted:
        """Deliver `cards` without artwork. Returns a handle `attach_artwork` can use."""
        ...

    async def attach_artwork(self, posted: Posted, cards: list[Card]) -> None:
        """Attach already-rendered artwork to an already-posted answer.

        Called only when at least one card carries `image` or `thumbnail` bytes - the
        caller decides whether there is anything to attach; this method's job is only
        how to attach it for this medium.
        """
        ...


class DiscordSink:
    """`OutputSink` over a Discord channel - a pure extraction of what `bot._answer`
    used to do inline, byte-for-byte, not a redesign. See `tests/test_sinks.py` for the
    behaviour this is held to.
    """

    def __init__(self, channel, capture: SessionCapture | None = None):
        self._channel = channel
        self._capture = capture
        self._message = None  # kept between post() and attach_artwork()

    async def stage(self, uid: str | None, stage: str) -> None:
        """No-op. Discord has no live progress surface for this bot; a card is the
        first thing it ever shows for a query."""

    async def post(self, cards: list[Card], *, feedback: bool) -> Posted:
        message = await self._channel.send(
            embeds=[to_embed(c, i, with_art=False) for i, c in enumerate(cards)],
            **({"view": FeedbackView(self._capture)}
               if (feedback and self._capture) else {}))
        self._message = message
        return Posted(message_id=message.id)

    async def attach_artwork(self, posted: Posted, cards: list[Card]) -> None:
        files = art_files(cards)
        if not files:
            return
        await self._message.edit(
            embeds=[to_embed(c, i) for i, c in enumerate(cards)], files=files)


# ---------------------------------------------------------------- local (Discord-free)

def _card_json(card: Card) -> dict:
    """A `Card`, minus artwork bytes - the console reads this straight into a message
    bubble. Image/thumbnail travel separately (`attach_artwork`, `_art_paths`) because
    they do not exist yet at `post()` time, same as Discord's own two-phase delivery."""
    return {"title": card.title, "lines": card.lines, "footer": card.footer,
           "colour": card.colour}


class LocalSink:
    """`OutputSink` writing to `data/sessions/<id>/chat.jsonl` and `art/`, for
    `output.medium = "local"`. See Docs/local-output-design.md §3.

    **Never raises into the answer path** - the same discipline `capture.py` and
    `spend.py` already hold themselves to. A player who cannot write to their own disk
    still deserves the card that was already computed; a broken write should degrade the
    Chat tab's history, not the answer in front of them.

    One instance per query, unlike `DiscordSink` which is also built fresh per call -
    `uid` is fixed at construction because every event this sink writes needs it, and
    passing it through every method call would just be threading a constructor argument
    by hand.
    """

    def __init__(self, session_dir: Path, uid: str):
        self._dir = Path(session_dir)
        self._uid = uid
        self._chat = self._dir / "chat.jsonl"
        self._art = self._dir / "art"

    def _append(self, row: dict) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            with self._chat.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("local sink: could not append to %s: %s", self._chat, e)

    async def stage(self, uid: str | None, stage: str) -> None:
        self._append({"uid": uid or self._uid, "kind": "stage", "stage": stage,
                      "at": time.time()})

    def record_query(self, text: str, at: float | None = None) -> None:
        """The player's OWN message, echoed into the same feed the console reads.

        Not part of `OutputSink` - `post()`/`attach_artwork()` only ever handle the
        ANSWER half, because a query is not a `Card` and never was one. Called by
        whatever consumes `inbox/` before `_answer` runs, so the console sees the
        question arrive before the router has even started (see the `stage()` event
        that follows it).
        """
        self._append({"uid": self._uid, "kind": "query", "role": "user",
                      "at": at if at is not None else time.time(), "text": text})

    async def post(self, cards: list[Card], *, feedback: bool) -> Posted:
        # `feedback` is accepted, not used - the console has no rendered equivalent of
        # Discord's buttons yet. Noted rather than silently dropped; see `sinks.py`'s
        # docstring on `FeedbackView` for the open question this leaves.
        self._append({"uid": self._uid, "kind": "answer", "role": "assistant",
                      "at": time.time(), "cards": [_card_json(c) for c in cards]})
        # None: this sink's own record is already keyed by `uid`, so there is nothing
        # for `capture.attach_message` to join - see `Posted`'s own docstring.
        return Posted(message_id=None)

    def _art_paths(self, index: int) -> tuple[Path, Path]:
        return (self._art / f"{self._uid}-image-{index}.jpg",
                self._art / f"{self._uid}-thumb-{index}.png")

    async def attach_artwork(self, posted: Posted, cards: list[Card]) -> None:
        # A separate, later event - not a rewrite of the `answer` row above, because
        # chat.jsonl is append-only by the same discipline `capture.py`'s log is. The
        # console merges by `uid` when it renders, the same way `capture.read_session`
        # already folds several lines for one utterance into one record.
        if not any(c.image or c.thumbnail for c in cards):
            # Mirrors DiscordSink's own `if not files: return` - defensive here too,
            # since a caller other than `_answer` (which already gates this) might not
            # check first. Checked BEFORE creating `art/`, so a query with nothing to
            # illustrate never leaves an empty directory behind.
            return
        images, thumbnails = [], []
        try:
            self._art.mkdir(parents=True, exist_ok=True)
            for i, c in enumerate(cards):
                image_path, thumb_path = self._art_paths(i)
                if c.image is not None:
                    image_path.write_bytes(c.image)
                    images.append(image_path.name)
                if c.thumbnail is not None:
                    thumb_path.write_bytes(Path(c.thumbnail).read_bytes())
                    thumbnails.append(thumb_path.name)
        except OSError as e:
            log.warning("local sink: could not write artwork under %s: %s",
                       self._art, e)
            return
        if not (images or thumbnails):
            return
        self._append({"uid": self._uid, "kind": "artwork", "at": time.time(),
                      "images": images, "thumbnails": thumbnails})
