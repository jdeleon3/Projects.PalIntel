"""The console's Chat tab backend (ADR-0018, steps 4-5).

Two layers, tested separately:

  * `palintel/ui/sources.py` — pure file reads and one file write, no server involved.
    `send_chat_query` is the one that WRITES from a caller-supplied `session`, which is
    why `valid_session_id` is exercised here directly rather than trusted implicitly.
  * `palintel/ui/server.py` — the HTTP layer, exercised with `aiohttp`'s own test
    client so the guard middleware, routing and the SSE resume-by-byte-offset math are
    all real, not mocked. `Config.load()` and the Supervisor are the two things that
    would otherwise reach outside `tmp_path`, so both are faked per test.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from palintel.ui import sources


# ================================================================== sources.py

def test_valid_session_id_matches_how_bot_py_mints_one():
    assert sources.valid_session_id(time.strftime("%Y%m%d-%H%M%S"))
    assert not sources.valid_session_id("../../etc")
    assert not sources.valid_session_id("20260814")
    assert not sources.valid_session_id("")


def test_chat_history_reads_rows_and_the_files_size(tmp_path):
    d = tmp_path / "20260814-090000"
    d.mkdir()
    rows = [{"uid": "a", "kind": "query", "text": "hi"},
            {"uid": "a", "kind": "answer", "cards": []}]
    text = "\n".join(json.dumps(r) for r in rows) + "\n"
    # Raw bytes, not `write_text` - text mode translates `\n` to `\r\n` on Windows, which
    # would make the size assertion below depend on the platform running the test.
    (d / "chat.jsonl").write_bytes(text.encode("utf-8"))

    got = sources.chat_history("20260814-090000", root=tmp_path)

    assert got["rows"] == rows
    assert got["size"] == len(text.encode("utf-8"))


def test_chat_history_is_empty_for_a_session_with_no_chat_yet(tmp_path):
    (tmp_path / "20260814-090000").mkdir()
    assert sources.chat_history("20260814-090000", root=tmp_path) == {"rows": [], "size": 0}


def test_chat_history_rejects_a_malformed_session_id(tmp_path):
    """No directory lookup at all for something that isn't a session id shape - the same
    defence `send_chat_query` needs, applied here too rather than only where it writes."""
    assert sources.chat_history("../../etc/passwd", root=tmp_path) == {"rows": [], "size": 0}


def test_latest_chat_session_picks_the_newest_with_a_chat_file(tmp_path):
    (tmp_path / "20260810-090000").mkdir()
    (tmp_path / "20260810-090000" / "chat.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "20260814-090000").mkdir()  # newer, but never wrote a chat.jsonl
    (tmp_path / "20260812-090000").mkdir()
    (tmp_path / "20260812-090000" / "chat.jsonl").write_text("", encoding="utf-8")

    assert sources.latest_chat_session(root=tmp_path) == "20260812-090000"


def test_latest_chat_session_is_none_when_nothing_qualifies(tmp_path):
    assert sources.latest_chat_session(root=tmp_path) is None
    (tmp_path / "20260814-090000").mkdir()
    assert sources.latest_chat_session(root=tmp_path) is None


def test_send_chat_query_writes_one_inbox_file(tmp_path):
    (tmp_path / "20260814-090000").mkdir()
    result = sources.send_chat_query("20260814-090000", "how do I beat Anubis",
                                     root=tmp_path)
    assert result["ok"]
    files = list((tmp_path / "20260814-090000" / "inbox").glob("*.json"))
    assert len(files) == 1
    row = json.loads(files[0].read_text(encoding="utf-8"))
    assert row["uid"] == result["uid"] and row["text"] == "how do I beat Anubis"


def test_send_chat_query_rejects_a_malformed_session_id(tmp_path):
    result = sources.send_chat_query("../../etc", "hello", root=tmp_path)
    assert not result["ok"]
    assert not (tmp_path / "inbox").exists()  # never even attempted to write


def test_send_chat_query_rejects_empty_text(tmp_path):
    (tmp_path / "20260814-090000").mkdir()
    result = sources.send_chat_query("20260814-090000", "   ", root=tmp_path)
    assert not result["ok"]


# --- art (step 6, §3.3) ----------------------------------------------------------

def test_art_path_finds_a_file_written_by_the_sink(tmp_path):
    art = tmp_path / "20260814-090000" / "art"
    art.mkdir(parents=True)
    (art / "abc-image-0.jpg").write_bytes(b"\xff\xd8\xff")

    path = sources.art_path("20260814-090000", "abc-image-0.jpg", root=tmp_path)

    assert path is not None and path.read_bytes() == b"\xff\xd8\xff"


def test_art_path_is_none_for_a_filename_that_does_not_exist(tmp_path):
    art = tmp_path / "20260814-090000" / "art"
    art.mkdir(parents=True)
    assert sources.art_path("20260814-090000", "nope.jpg", root=tmp_path) is None


def test_art_path_rejects_a_traversal_attempt(tmp_path):
    """The filename is checked against the directory LISTING, not joined blind - a
    `../` component must never resolve to something outside `art/`, even if a file of
    that literal name happens to exist elsewhere."""
    (tmp_path / "secret.jpg").write_bytes(b"not yours")
    (tmp_path / "20260814-090000" / "art").mkdir(parents=True)

    path = sources.art_path("20260814-090000", "../../secret.jpg", root=tmp_path)

    assert path is None


def test_art_path_rejects_a_malformed_session_id(tmp_path):
    assert sources.art_path("../../etc", "x.jpg", root=tmp_path) is None


# ==================================================================== server.py

class _FakeSupervisor:
    def __init__(self, status: dict):
        self._status = status

    def status(self) -> dict:
        return self._status


def _free_port() -> int:
    """A real OS-assigned port, picked before the server starts - `build_app` bakes its
    `port` argument into the guard's Origin allow-list at construction time, so the
    `TestServer` needs to be told to bind that SAME port rather than one aiohttp only
    reveals after starting."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_app(monkeypatch, tmp_path, *, medium: str, supervisor_status: dict,
              token: str = "tok"):
    """A real `build_app`, with the two things that would otherwise reach outside
    `tmp_path` (config, the bot heartbeat) swapped for fakes - everything else,
    including the guard middleware and the route table, is the genuine article.

    Returns `(app, port)` - the port is the one `TestServer` must be told to bind, so
    it matches what `guard` already baked into its Origin allow-list.
    """
    from palintel.ui import server
    from palintel.config import Config

    class _Cfg:
        class output:
            pass
    _Cfg.output.medium = medium
    monkeypatch.setattr(Config, "load", classmethod(lambda cls, *a, **kw: _Cfg()))
    monkeypatch.setattr(sources, "SESSIONS", tmp_path)

    port = _free_port()
    app = server.build_app(token, port)
    app["supervisor"] = _FakeSupervisor(supervisor_status)
    return app, port


def _headers(token: str = "tok") -> dict:
    return {"X-PalIntel-Token": token}


def test_current_is_hidden_under_discord_medium(monkeypatch, tmp_path):
    app, port = _make_app(monkeypatch, tmp_path, medium="discord", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get("/api/chat/current", headers=_headers())
            return await r.json()

    assert asyncio.run(scenario()) == {"visible": False}


def test_current_is_empty_under_local_medium_with_nothing_on_disk(monkeypatch, tmp_path):
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get("/api/chat/current", headers=_headers())
            return await r.json()

    assert asyncio.run(scenario()) == {"visible": True, "live": False, "session": None}


def test_current_is_live_when_the_local_bot_is_running(monkeypatch, tmp_path):
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={
        "running": True, "output": "local", "session": "20260814-090000"})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get("/api/chat/current", headers=_headers())
            return await r.json()

    assert asyncio.run(scenario()) == {
        "visible": True, "live": True, "session": "20260814-090000"}


def test_current_is_read_only_when_a_discord_heartbeat_is_running(monkeypatch, tmp_path):
    """A running DISCORD bot must not read as a live Chat tab just because something is
    running - only a heartbeat that says `output: "local"` counts."""
    (tmp_path / "20260812-090000").mkdir()
    (tmp_path / "20260812-090000" / "chat.jsonl").write_text("", encoding="utf-8")
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={
        "running": True, "session": "20260814-090000"})  # no "output" key - Discord's beat()

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get("/api/chat/current", headers=_headers())
            return await r.json()

    assert asyncio.run(scenario()) == {
        "visible": True, "live": False, "session": "20260812-090000"}


def test_history_returns_rows_and_size(monkeypatch, tmp_path):
    d = tmp_path / "20260814-090000"
    d.mkdir()
    (d / "chat.jsonl").write_text('{"uid": "a", "kind": "query", "text": "hi"}\n',
                                  encoding="utf-8")
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get("/api/chat/20260814-090000/history", headers=_headers())
            return r.status, await r.json()

    status, body = asyncio.run(scenario())
    assert status == 200
    assert body["rows"] == [{"uid": "a", "kind": "query", "text": "hi"}]
    assert body["size"] > 0


def test_history_404s_on_a_malformed_session_id(monkeypatch, tmp_path):
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get("/api/chat/not-a-session/history", headers=_headers())
            return r.status

    assert asyncio.run(scenario()) == 404


def test_art_serves_a_file_the_sink_wrote(monkeypatch, tmp_path):
    art = tmp_path / "20260814-090000" / "art"
    art.mkdir(parents=True)
    (art / "abc-image-0.jpg").write_bytes(b"\xff\xd8\xff" * 4)
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get(
                "/api/sessions/20260814-090000/art/abc-image-0.jpg", headers=_headers())
            return r.status, r.content_type, await r.read()

    status, ctype, body = asyncio.run(scenario())
    assert status == 200
    assert ctype == "image/jpeg"
    assert body == b"\xff\xd8\xff" * 4


def test_art_404s_for_a_filename_that_was_never_written(monkeypatch, tmp_path):
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.get(
                "/api/sessions/20260814-090000/art/nope.jpg", headers=_headers())
            return r.status

    assert asyncio.run(scenario()) == 404


def test_send_needs_an_origin_but_history_does_not(monkeypatch, tmp_path):
    """The `guard` middleware's own POST-vs-GET distinction, proven against a real Chat
    route rather than only against `/api/config` - a cross-site POST cannot forge
    `Origin`, so a write with the wrong one must be refused."""
    (tmp_path / "20260814-090000").mkdir()
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            no_origin = await client.post(
                "/api/chat/20260814-090000/send",
                json={"text": "hi"}, headers=_headers())
            good_origin = await client.post(
                "/api/chat/20260814-090000/send", json={"text": "hi"},
                headers={**_headers(), "Origin": f"http://127.0.0.1:{client.port}"})
            return no_origin.status, good_origin.status

    refused, ok = asyncio.run(scenario())
    assert refused == 403
    assert ok == 200


def test_send_writes_an_inbox_file_the_bot_can_claim(monkeypatch, tmp_path):
    (tmp_path / "20260814-090000").mkdir()
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            r = await client.post(
                "/api/chat/20260814-090000/send", json={"text": "how do I beat Anubis"},
                headers={**_headers(), "Origin": f"http://127.0.0.1:{client.port}"})
            return await r.json()

    body = asyncio.run(scenario())
    assert body["ok"]
    files = list((tmp_path / "20260814-090000" / "inbox").glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["text"] == "how do I beat Anubis"


def test_stream_delivers_appended_lines_and_resumes_by_last_event_id(monkeypatch, tmp_path):
    """The one piece of real logic in the SSE handler: `after` seeds the first
    connection, and a SECOND connection using the `id:` the first one sent resumes
    from exactly that point rather than replaying the first line."""
    d = tmp_path / "20260814-090000"
    d.mkdir()
    chat = d / "chat.jsonl"
    first = json.dumps({"uid": "a", "kind": "query", "text": "one"}) + "\n"
    chat.write_text(first, encoding="utf-8")
    app, port = _make_app(monkeypatch, tmp_path, medium="local", supervisor_status={})

    async def read_one_event(resp) -> tuple[str, str]:
        """One `id:`/`data:` pair, skipping any keep-alive comment lines."""
        event_id = None
        while True:
            line = await asyncio.wait_for(resp.content.readline(), timeout=5)
            line = line.decode("utf-8").rstrip("\n")
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                return event_id, line[6:]
            # blank lines and ": ping" comments are skipped

    async def scenario():
        async with TestClient(TestServer(app, port=port)) as client:
            resp = await client.get(
                "/api/chat/20260814-090000/stream?after=0", headers=_headers())
            first_id, first_data = await read_one_event(resp)
            resp.close()

            # A second line, appended after the first connection already saw the first.
            with chat.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"uid": "b", "kind": "query", "text": "two"}) + "\n")

            resp2 = await client.get(
                "/api/chat/20260814-090000/stream",
                headers={**_headers(), "Last-Event-ID": first_id})
            second_id, second_data = await read_one_event(resp2)
            resp2.close()
            return first_data, second_data

    first_data, second_data = asyncio.run(scenario())
    assert json.loads(first_data)["text"] == "one"
    # Resumed past the first line, not replayed from the start.
    assert json.loads(second_data)["text"] == "two"
