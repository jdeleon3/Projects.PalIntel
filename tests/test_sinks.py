"""`DiscordSink` — a pure extraction of what `bot._answer` used to do inline.

These tests exist because the full suite passing unchanged after the extraction proves
no *existing* test regressed, not that the new code path is exercised at all - nothing
before this file called `_answer` against a fake channel and checked what actually went
out. See `sinks.py` and Docs/local-output-design.md.
"""
from __future__ import annotations

import asyncio
import json

from palintel.capture import SessionCapture
from palintel.cards import TIER_FACT, Card
from palintel.sinks import DiscordSink, FeedbackView, LocalSink, Posted


class _FakeMessage:
    def __init__(self, id_: int):
        self.id = id_
        self.edits: list[dict] = []

    async def edit(self, **kw):
        self.edits.append(kw)


class _FakeChannel:
    """Records every `send`, and hands back a message with a fresh, fake id."""

    def __init__(self):
        self.sends: list[dict] = []
        self._next_id = 1000

    async def send(self, **kw):
        self.sends.append(kw)
        msg = _FakeMessage(self._next_id)
        self._next_id += 1
        return msg


def card(**kw) -> Card:
    base = dict(title="Coal locations", lines=["**1.** (10, 20)"], footer="1 cluster",
               colour=TIER_FACT)
    return Card(**{**base, **kw})


# ------------------------------------------------------------------------------ post()

def test_post_sends_embeds_without_art():
    channel = _FakeChannel()
    sink = DiscordSink(channel)
    posted = asyncio.run(sink.post([card()], feedback=False))

    assert len(channel.sends) == 1
    embeds = channel.sends[0]["embeds"]
    assert len(embeds) == 1 and embeds[0].title == "Coal locations"
    assert "view" not in channel.sends[0]
    assert isinstance(posted, Posted) and posted.message_id == 1000


def test_post_returns_the_message_id_as_the_capture_join_key():
    channel = _FakeChannel()
    sink = DiscordSink(channel)
    posted = asyncio.run(sink.post([card()], feedback=False))
    # Discord IS a sink that needs a separate join key - see Posted's own docstring.
    assert posted.message_id is not None


def test_feedback_view_rides_in_the_same_send_when_requested(tmp_path):
    capture = SessionCapture(root=tmp_path, session="s")
    channel = _FakeChannel()
    sink = DiscordSink(channel, capture)
    asyncio.run(sink.post([card()], feedback=True))

    assert isinstance(channel.sends[0]["view"], FeedbackView)


def test_no_feedback_view_without_capture_even_if_requested():
    """Mirrors `_answer`'s own guard - `feedback and capture` - so a sink built with no
    capture never renders controls nothing will ever read."""
    channel = _FakeChannel()
    sink = DiscordSink(channel, capture=None)
    asyncio.run(sink.post([card()], feedback=True))
    assert "view" not in channel.sends[0]


def test_two_cards_become_two_embeds_in_one_message():
    """One message, several embeds - a base Pal and its variant is two correct answers,
    and separate messages would let channel traffic interleave and break the pairing."""
    channel = _FakeChannel()
    sink = DiscordSink(channel)
    asyncio.run(sink.post(
        [card(title="Chillet"), card(title="Chillet Ignis")], feedback=False))
    assert len(channel.sends[0]["embeds"]) == 2


# ---------------------------------------------------------------------- attach_artwork()

def test_attach_artwork_edits_the_posted_message():
    channel = _FakeChannel()
    sink = DiscordSink(channel)
    posted = asyncio.run(sink.post([card()], feedback=False))
    illustrated = card(image=b"\xff\xd8\xff" * 10)  # a plausible JPEG-ish blob

    asyncio.run(sink.attach_artwork(posted, [illustrated]))

    assert len(channel.sends[0]["embeds"])  # the post still happened
    edited = sink._message.edits
    assert len(edited) == 1
    assert len(edited[0]["files"]) == 1


def test_attach_artwork_does_nothing_when_the_cards_carry_no_art():
    """Defensive - `_answer` already gates this call on `any(image or thumbnail)`, but a
    sink used some other way must not misbehave if asked anyway."""
    channel = _FakeChannel()
    sink = DiscordSink(channel)
    posted = asyncio.run(sink.post([card()], feedback=False))

    asyncio.run(sink.attach_artwork(posted, [card()]))  # no image, no thumbnail

    assert sink._message.edits == []


# ------------------------------------------------------- _answer's default sink (bot.py)

def test_answer_defaults_to_a_discord_sink_built_from_channel(tmp_path):
    """The strongest proof the extraction is behaviour-preserving: every EXISTING call
    site (`on_message`, the voice path) passes `channel` and never `sink`, so this is
    what actually runs in production, not just `DiscordSink` exercised in isolation."""
    from palintel import bot
    from palintel.knowledge import KnowledgeBase
    from palintel.pipeline import Pipeline, PlayerState
    from palintel.tools import ToolCall

    class _FixedRouter:
        name = "fixed"

        def route(self, utterance, candidates, context=None):
            return ToolCall("find_resource_nodes", {"resource": "coal"})

    kb = KnowledgeBase.load("1.0.2")
    pipe = Pipeline(kb, _FixedRouter())
    channel = _FakeChannel()

    asyncio.run(bot._answer(channel, pipe, "where's the nearest coal", "tester"))

    assert len(channel.sends) == 1
    assert "Coal" in channel.sends[0]["embeds"][0].title


def test_answer_stages_routing_before_calling_the_pipeline():
    """The one live progress signal this project makes - see ADR-0018. Checked by a
    fake pipeline that records whether a stage event already arrived by the time it
    runs, not just that `stage()` was called at some point."""
    from palintel import bot
    from palintel.knowledge import KnowledgeBase
    from palintel.pipeline import Pipeline, PlayerState
    from palintel.sinks import Posted
    from palintel.tools import ToolCall

    class _FixedRouter:
        name = "fixed"

        def route(self, utterance, candidates, context=None):
            return ToolCall("find_resource_nodes", {"resource": "coal"})

    class _RecordingSink:
        def __init__(self):
            self.staged_before_post = False
            self._staged = False

        async def stage(self, uid, stage):
            self._staged = True

        async def post(self, cards, *, feedback):
            self.staged_before_post = self._staged
            return Posted(message_id=None)

        async def attach_artwork(self, posted, cards):
            pass

    kb = KnowledgeBase.load("1.0.2")
    pipe = Pipeline(kb, _FixedRouter())
    sink = _RecordingSink()

    asyncio.run(bot._answer(None, pipe, "where's the nearest coal", "tester", sink=sink))

    assert sink.staged_before_post


def test_a_pipeline_failure_posts_a_card_through_the_sink_not_a_raw_embed():
    """Used to be `channel.send(embed=discord.Embed(...))` unconditionally - a path
    that only ever worked for Discord. Now a `Card`, which every sink can render."""
    from palintel import bot
    from palintel.knowledge import KnowledgeBase
    from palintel.pipeline import Pipeline

    class _BrokenRouter:
        name = "broken"

        def route(self, utterance, candidates, context=None):
            raise RuntimeError("boom")

    kb = KnowledgeBase.load("1.0.2")
    pipe = Pipeline(kb, _BrokenRouter())
    channel = _FakeChannel()

    asyncio.run(bot._answer(channel, pipe, "how do I beat Anubis", "tester"))

    assert len(channel.sends) == 1
    assert channel.sends[0]["embeds"][0].title == "Something broke"


# ========================================================================= LocalSink

def _rows(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def test_record_query_appends_a_user_row(tmp_path):
    sink = LocalSink(tmp_path, uid="u0")
    sink.record_query("how do I beat Anubis", at=123.0)
    rows = _rows(tmp_path / "chat.jsonl")
    assert rows == [{"uid": "u0", "kind": "query", "role": "user", "at": 123.0,
                     "text": "how do I beat Anubis"}]


def test_stage_appends_a_stage_row(tmp_path):
    sink = LocalSink(tmp_path, uid="u1")
    asyncio.run(sink.stage("u1", "routing_started"))
    rows = _rows(tmp_path / "chat.jsonl")
    assert len(rows) == 1
    assert rows[0]["kind"] == "stage" and rows[0]["stage"] == "routing_started"
    assert rows[0]["uid"] == "u1"


def test_post_appends_an_answer_row_with_no_join_key(tmp_path):
    """`message_id` is None - LocalSink's own record is already keyed by `uid`, so
    there is nothing for `capture.attach_message` to join, unlike Discord's snowflake."""
    sink = LocalSink(tmp_path, uid="u2")
    posted = asyncio.run(sink.post([card(title="Coal locations")], feedback=False))
    rows = _rows(tmp_path / "chat.jsonl")

    assert posted.message_id is None
    assert len(rows) == 1
    assert rows[0]["role"] == "assistant"
    assert rows[0]["cards"][0]["title"] == "Coal locations"


def test_post_carries_no_art_fields_yet(tmp_path):
    """Artwork does not exist at post() time - same two-phase shape Discord uses.
    `attach_artwork` is what writes it, as a SEPARATE later event."""
    sink = LocalSink(tmp_path, uid="u3")
    asyncio.run(sink.post([card()], feedback=False))
    row = _rows(tmp_path / "chat.jsonl")[0]
    assert "image" not in row["cards"][0] and "thumbnail" not in row["cards"][0]


def test_attach_artwork_writes_files_and_a_separate_event(tmp_path):
    sink = LocalSink(tmp_path, uid="u4")
    posted = asyncio.run(sink.post([card()], feedback=False))
    illustrated = card(image=b"\xff\xd8\xff" * 10)

    asyncio.run(sink.attach_artwork(posted, [illustrated]))

    written = list((tmp_path / "art").glob("u4-image-*.jpg"))
    assert len(written) == 1
    assert written[0].read_bytes() == b"\xff\xd8\xff" * 10

    rows = _rows(tmp_path / "chat.jsonl")
    assert len(rows) == 2  # the answer row, then the artwork row
    assert rows[1]["kind"] == "artwork"
    assert rows[1]["images"] == [written[0].name]


def test_attach_artwork_writes_nothing_when_the_cards_carry_no_art(tmp_path):
    sink = LocalSink(tmp_path, uid="u5")
    posted = asyncio.run(sink.post([card()], feedback=False))
    asyncio.run(sink.attach_artwork(posted, [card()]))  # no image, no thumbnail

    assert not (tmp_path / "art").exists()
    assert len(_rows(tmp_path / "chat.jsonl")) == 1  # only the answer row


def test_local_sink_never_raises_on_an_unwritable_directory(tmp_path):
    """Same discipline `capture.py` and `spend.py` already hold: a player who cannot
    write to their own disk still deserves the card that was already computed."""
    blocker = tmp_path / "sessions"
    blocker.write_text("not a directory", encoding="utf-8")
    sink = LocalSink(blocker / "s1", uid="u6")

    asyncio.run(sink.stage("u6", "routing_started"))          # must not raise
    posted = asyncio.run(sink.post([card()], feedback=False))  # must not raise
    asyncio.run(sink.attach_artwork(                           # must not raise
        posted, [card(image=b"\xff\xd8\xff")]))
    assert posted.message_id is None


# =============================================================== local inbox (bot.py)
#
# `run_local()` itself is not exercised here - it does `Config.load()`, builds real
# background loops and calls `asyncio.run()`, which is exactly the shape of thing this
# project's own tests avoid starting for real (see test_bot.py's config tests, which
# never call `run()` either). What CAN be proven in isolation is the one-file unit of
# work every poll tick performs, and that `main()` reads the router bit and picks the
# right function without running either one.

def _fixed_pipe():
    from palintel.knowledge import KnowledgeBase
    from palintel.pipeline import Pipeline
    from palintel.tools import ToolCall

    class _FixedRouter:
        name = "fixed"

        def route(self, utterance, candidates, context=None):
            return ToolCall("find_resource_nodes", {"resource": "coal"})

    return Pipeline(KnowledgeBase.load("1.0.2"), _FixedRouter())


def test_handle_inbox_file_answers_and_deletes_the_file(tmp_path):
    from palintel import bot
    from palintel import identity
    from palintel.activity import ActivityLog

    session_dir = tmp_path / "s1"
    inbox = session_dir / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "u1.json"
    path.write_text(json.dumps({"uid": "u1", "text": "where's the nearest coal",
                                "at": 111.0}), encoding="utf-8")

    asyncio.run(bot._handle_inbox_file(
        path, session_dir, _fixed_pipe(), ActivityLog(), watcher=None,
        bindings=identity.Bindings(path=tmp_path / "bindings.json"),
        capture=None, spend=None))

    assert not path.exists()  # claimed before the answer was even rendered
    rows = _rows(session_dir / "chat.jsonl")
    # query -> "routing_started" stage (ADR-0018) -> answer
    assert [r["kind"] for r in rows] == ["query", "stage", "answer"]
    assert rows[0]["uid"] == "u1" and rows[0]["text"] == "where's the nearest coal"
    assert "Coal" in rows[2]["cards"][0]["title"]


def test_handle_inbox_file_discards_a_file_with_no_text(tmp_path):
    """No answer to give, so nothing to route - and no half-formed row left behind for
    the console to render."""
    from palintel import bot
    from palintel import identity
    from palintel.activity import ActivityLog

    session_dir = tmp_path / "s2"
    inbox = session_dir / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "u2.json"
    path.write_text(json.dumps({"uid": "u2", "text": "   "}), encoding="utf-8")

    asyncio.run(bot._handle_inbox_file(
        path, session_dir, _fixed_pipe(), ActivityLog(), watcher=None,
        bindings=identity.Bindings(path=tmp_path / "bindings.json"),
        capture=None, spend=None))

    assert not path.exists()
    assert _rows(session_dir / "chat.jsonl") == []


def test_handle_inbox_file_discards_unreadable_json(tmp_path):
    """A single malformed file must not take the poll loop down - see `_poll_inbox`'s
    own try/except around this call."""
    from palintel import bot
    from palintel import identity
    from palintel.activity import ActivityLog

    session_dir = tmp_path / "s3"
    inbox = session_dir / "inbox"
    inbox.mkdir(parents=True)
    path = inbox / "u3.json"
    path.write_text("{not json", encoding="utf-8")

    asyncio.run(bot._handle_inbox_file(
        path, session_dir, _fixed_pipe(), ActivityLog(), watcher=None,
        bindings=identity.Bindings(path=tmp_path / "bindings.json"),
        capture=None, spend=None))

    assert not path.exists()
    assert _rows(session_dir / "chat.jsonl") == []


def test_poll_inbox_picks_up_a_file_written_after_the_loop_starts(tmp_path):
    """Proves the loop itself, not just the per-file handler: a file dropped into
    `inbox/` mid-poll gets claimed on the next tick, not just on the first one."""
    from palintel import bot
    from palintel import identity
    from palintel.activity import ActivityLog

    async def scenario():
        session_dir = tmp_path / "s4"
        task = asyncio.create_task(bot._poll_inbox(
            session_dir, _fixed_pipe(), ActivityLog(), None,
            identity.Bindings(path=tmp_path / "bindings.json"), None, None,
            poll_s=0.01))
        try:
            await asyncio.sleep(0.03)  # let it tick at least once against an empty inbox
            inbox = session_dir / "inbox"
            inbox.mkdir(parents=True, exist_ok=True)
            (inbox / "u4.json").write_text(
                json.dumps({"uid": "u4", "text": "where's the nearest coal"}),
                encoding="utf-8")
            chat = session_dir / "chat.jsonl"
            for _ in range(50):  # poll for the answer row, rather than a fixed sleep
                if chat.exists() and any(
                        json.loads(line)["kind"] == "answer"
                        for line in chat.read_text(encoding="utf-8").splitlines()):
                    break
                await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return session_dir

    session_dir = asyncio.run(scenario())
    assert not (session_dir / "inbox" / "u4.json").exists()
    rows = _rows(session_dir / "chat.jsonl")
    assert [r["kind"] for r in rows] == ["query", "stage", "answer"]


def test_main_starts_the_local_run_when_configured(monkeypatch):
    from palintel import bot

    class _Cfg:
        class output:
            medium = "local"

    calls = []
    monkeypatch.setattr(bot.Config, "load", classmethod(lambda cls: _Cfg()))
    monkeypatch.setattr(bot, "run_local", lambda: calls.append("local"))
    monkeypatch.setattr(bot, "run", lambda: calls.append("discord"))

    bot.main()

    assert calls == ["local"]


# ========================================================== local voice (bot.py)
#
# `MicListener.start()` opens a real audio device and `Transcriber(...)` loads a real
# STT model - neither is something any test in this project starts for real (there is
# no existing coverage of `start_voice()` either, Discord's own twin). Both are faked
# here; everything downstream of the fake - activation gating, `_answer` reached with
# the right sink, the result landing in `chat.jsonl` - is real and is what these prove.

def test_start_voice_local_answers_through_local_sink(tmp_path, monkeypatch):
    from palintel import bot, identity
    from palintel import mic as mic_mod
    from palintel import stt as stt_mod
    from palintel.activity import ActivityLog
    from palintel.config import Config, DiscordConfig, OutputConfig, VoiceConfig
    from palintel.listening import Utterance

    captured = {}

    class _FakeMic:
        def __init__(self, on_utterance, **kw):
            captured["dispatch"] = on_utterance

        def start(self):
            captured["started"] = True

    class _FakeTranscriber:
        def __init__(self, lexicon):
            self.device = "cpu"

        def transcribe(self, path):
            return "hey pal where's the nearest coal"

    monkeypatch.setattr(mic_mod, "MicListener", _FakeMic)
    monkeypatch.setattr(stt_mod, "Transcriber", _FakeTranscriber)

    cfg = Config(discord=DiscordConfig(token="", channel_id=0),
                voice=VoiceConfig(enabled=True, source="mic"),
                output=OutputConfig(medium="local"))
    session_dir = tmp_path / "s1"
    bindings = identity.Bindings(path=tmp_path / "bindings.json")

    async def scenario():
        listener = await bot._start_voice_local(
            cfg, _fixed_pipe(), ActivityLog(), None, bindings, None, None, session_dir)
        assert captured.get("started")
        # A closed 1s buffer - only `.frames` needs to be plausible, since the fake
        # transcriber ignores the audio entirely.
        utt = Utterance(pcm=b"\x00\x00" * 800, reason="silence", frames=50)
        captured["dispatch"](utt)  # simulates the mic's own callback thread
        # `dispatch` hands off via `run_coroutine_threadsafe` onto this same loop -
        # give it a tick to actually run before asserting on its result. Waiting for
        # the FILE to exist is not enough - the "stage" row lands first and would end
        # the wait before "answer" is ever written.
        chat = session_dir / "chat.jsonl"
        for _ in range(50):
            if chat.exists() and any(
                    json.loads(line)["kind"] == "answer"
                    for line in chat.read_text(encoding="utf-8").splitlines()):
                break
            await asyncio.sleep(0.01)
        return listener

    listener = asyncio.run(scenario())
    assert listener is not None
    rows = _rows(session_dir / "chat.jsonl")
    # No "query" row - unlike the inbox path, nothing was ever typed to echo back.
    assert [r["kind"] for r in rows] == ["stage", "answer"]
    assert "Coal" in rows[1]["cards"][0]["title"]


def test_start_voice_local_declines_a_bare_wake_word_through_local_sink(
        tmp_path, monkeypatch):
    """The wake word fired and endpointing closed before any question - ADR-0004's
    "player speaks, nothing happens" failure. A confident bare match still posts a
    card, through the sink, same as the Discord path posts a plain message."""
    from palintel import bot, identity
    from palintel import mic as mic_mod
    from palintel import stt as stt_mod
    from palintel.activity import ActivityLog
    from palintel.config import Config, DiscordConfig, OutputConfig, VoiceConfig
    from palintel.listening import Utterance

    captured = {}

    class _FakeMic:
        def __init__(self, on_utterance, **kw):
            captured["dispatch"] = on_utterance

        def start(self):
            pass

    class _FakeTranscriber:
        def __init__(self, lexicon):
            self.device = "cpu"

        def transcribe(self, path):
            return "hey pal"  # wake word, no query - endpointing cut it short

    monkeypatch.setattr(mic_mod, "MicListener", _FakeMic)
    monkeypatch.setattr(stt_mod, "Transcriber", _FakeTranscriber)

    cfg = Config(discord=DiscordConfig(token="", channel_id=0),
                voice=VoiceConfig(enabled=True, source="mic"),
                output=OutputConfig(medium="local"))
    session_dir = tmp_path / "s2"
    bindings = identity.Bindings(path=tmp_path / "bindings.json")

    async def scenario():
        await bot._start_voice_local(
            cfg, _fixed_pipe(), ActivityLog(), None, bindings, None, None, session_dir)
        utt = Utterance(pcm=b"\x00\x00" * 800, reason="silence", frames=50)
        captured["dispatch"](utt)
        for _ in range(50):
            if (session_dir / "chat.jsonl").exists():
                break
            await asyncio.sleep(0.01)

    asyncio.run(scenario())
    rows = _rows(session_dir / "chat.jsonl")
    assert len(rows) == 1 and rows[0]["kind"] == "answer"
    assert rows[0]["cards"][0]["title"] == "Didn't catch a question"


def test_main_starts_the_discord_run_by_default(monkeypatch):
    from palintel import bot

    class _Cfg:
        class output:
            medium = "discord"

    calls = []
    monkeypatch.setattr(bot.Config, "load", classmethod(lambda cls: _Cfg()))
    monkeypatch.setattr(bot, "run_local", lambda: calls.append("local"))
    monkeypatch.setattr(bot, "run", lambda: calls.append("discord"))

    bot.main()

    assert calls == ["discord"]
