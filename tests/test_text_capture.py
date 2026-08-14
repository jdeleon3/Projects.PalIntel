"""The text channel captures and offers feedback, the same as voice.

Both defects here were found by PLAYING, not by testing, and they had the same shape: a
capability built for both channels, wired into one.

On 2026-08-13 the test plan was deliberately run over text - to take routing readings
without STT errors in them - and the `_answer` call for that path passed neither
`capture` nor `feedback`. So there were no buttons to press, and not one of those 61
queries reached `log.jsonl`: `analyse_session.py` reported 3 utterances in a 64-query
session and nothing looked wrong, because 3 utterances is a plausible number.

The second is the speaker name. `guild.get_member` reads a cache that the privileged
members intent fills, and that intent is off, so it returns None forever - 10 queries
attributed to `speaker 366300806208552972`. The fallback was correct; the lookup it
guarded could not have worked.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path

BOT = Path(__file__).resolve().parents[1] / "palintel" / "bot.py"


def _call_named(func_name: str, *, inside: str):
    """Every call to `func_name` lexically inside `inside`, as AST nodes."""
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != inside:
            continue
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and getattr(call.func, "id", None) == func_name:
                yield call


def _kwargs(call) -> set[str]:
    return {k.arg for k in call.keywords if k.arg}


# --- the capture/feedback wiring -----------------------------------------------

def test_the_text_path_passes_capture_and_feedback():
    """Read off the source rather than by running a bot, because the defect was an
    ABSENT argument. A behavioural test needs a fully wired client to notice that
    nothing happened, and "nothing happened" is exactly what the bug looked like in
    production for a whole session."""
    calls = list(_call_named("_answer", inside="on_message"))
    assert calls, "no _answer call found in on_message - has it been renamed?"
    for call in calls:
        got = _kwargs(call)
        assert "capture" in got, (
            "the text path must pass capture=, or typed queries are never recorded")
        assert "feedback" in got, (
            "the text path must pass feedback=, or answers get no feedback buttons")
        assert "uid" in got, "capture.record is skipped without a uid"


def test_both_channels_pass_the_same_capture_arguments():
    """The general rule, not a patch for the one call site that was wrong: whatever the
    voice path records with, the text path records with too."""
    shared = {"capture", "uid", "feedback"}
    for where in ("on_message", "on_speech"):
        calls = [c for c in _call_named("_answer", inside=where)]
        assert calls, f"no _answer call in {where}"
        for call in calls:
            assert shared <= _kwargs(call), f"{where} is missing {shared - _kwargs(call)}"


def test_the_recorded_wav_is_conditional_on_the_channel():
    """There is no clip on the text path. Naming one would put a file reference in the
    corpus that resolves to nothing - and the STT scorers take their file list from the
    directory listing, so the lie would sit there unread until something trusted it."""
    tree = ast.parse(BOT.read_text(encoding="utf-8"))
    wav = [k.value for node in ast.walk(tree)
           if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Utterance"
           for k in node.keywords if k.arg == "wav"]
    assert wav, "no Utterance(wav=...) found"
    assert any(isinstance(v, ast.IfExp) for v in wav), (
        "wav must depend on channel_kind, not be an unconditional f'{uid}.wav'")


def test_the_text_uid_is_distinguishable_from_a_voice_one():
    """Both land in one `log.jsonl`, and the analyser needs to tell a typed row from a
    spoken one without opening the clip directory."""
    src = BOT.read_text(encoding="utf-8")
    assert 'uid=f"t{' in src, "text uids should carry a marker distinct from voice uids"


# --- the speaker name ----------------------------------------------------------

def test_the_members_intent_is_not_enabled():
    """It is privileged. py-cord raises PrivilegedIntentsRequired at LOGIN when the portal
    toggle is off, so setting it here trades a cosmetic failure (an id in a ledger) for a
    total one (a bot that will not connect). The REST path needs no intent at all."""
    src = BOT.read_text(encoding="utf-8")
    assert "intents.members = True" not in src
    assert "intents.message_content = True" in src, "this one IS required"



def test_an_uncached_speaker_is_resolved_over_rest():
    from palintel import bot

    class _Member:
        display_name = "Ruichan"

    class _Guild:
        async def fetch_member(self, uid):
            assert uid == 366300806208552972
            return _Member()

    class _Client:
        voice_clients = [type("VC", (), {"channel": type("C", (), {"guild": _Guild()})()})()]

    speaker = type("Object", (), {"id": 366300806208552972})()
    cache: dict = {}
    assert asyncio.run(bot.resolve_speaker(_Client(), speaker, cache)) == "Ruichan"
    assert cache == {366300806208552972: "Ruichan"}



def test_the_result_is_cached_so_one_lookup_serves_the_session():
    from palintel import bot

    calls = []

    class _Guild:
        async def fetch_member(self, uid):
            calls.append(uid)
            return type("M", (), {"display_name": "Ruichan"})()

    class _Client:
        voice_clients = [type("VC", (), {"channel": type("C", (), {"guild": _Guild()})()})()]

    speaker = type("Object", (), {"id": 7})()
    cache: dict = {}
    for _ in range(5):
        assert asyncio.run(bot.resolve_speaker(_Client(), speaker, cache)) == "Ruichan"
    assert calls == [7], "this is on the path of every utterance"



def test_a_guild_miss_falls_through_to_the_user_endpoint():
    """A member who left the guild 404s on fetch_member and still has a global name."""
    from palintel import bot

    class _Guild:
        async def fetch_member(self, uid):
            raise RuntimeError("404 Not Found")

    class _Client:
        voice_clients = [type("VC", (), {"channel": type("C", (), {"guild": _Guild()})()})()]

        async def fetch_user(self, uid):
            return type("U", (), {"display_name": "Ruichan"})()

    speaker = type("Object", (), {"id": 7})()
    assert asyncio.run(bot.resolve_speaker(_Client(), speaker, {})) == "Ruichan"



def test_an_unresolvable_id_keeps_the_honest_fallback_and_is_cached():
    """A deleted account resolves nowhere. `speaker <id>` rather than a Python repr, and
    cached: retrying once per sentence buys the same answer at the cost of a stall on the
    voice path."""
    from palintel import bot

    class _Client:
        voice_clients = []

        async def fetch_user(self, uid):
            raise RuntimeError("404")

    speaker = type("Object", (), {"id": 366300806208552972})()
    cache: dict = {}
    got = asyncio.run(bot.resolve_speaker(_Client(), speaker, cache))
    assert got == "speaker 366300806208552972"
    assert "Object" not in got, "never leak a Python repr into data read back later"
    assert cache[366300806208552972] == got



def test_a_real_member_needs_no_lookup_at_all():
    from palintel import bot

    class _Client:
        voice_clients = []

        async def fetch_user(self, uid):
            raise AssertionError("should not have been called")

    speaker = type("M", (), {"id": 7, "display_name": "Ruichan"})()
    assert asyncio.run(bot.resolve_speaker(_Client(), speaker, {})) == "Ruichan"
