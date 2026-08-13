"""The console's HTTP layer — loopback only, token-gated, read-only in this phase.

**Why a token on a page that only listens on 127.0.0.1.** Any web page open in the same
browser can issue requests to localhost; loopback binding stops the network, not the
browser. That is harmless for a dashboard and is not harmless for the control surface this
grows into, where a request rewrites `config.local.toml` and restarts a process. Three
cheap defences, applied now rather than retrofitted around endpoints that already exist:

  1. **Bind 127.0.0.1**, so nothing off this machine can reach it at all.
  2. **A token**, minted per run and printed in the URL. A page that does not know it
     cannot read your session audio or your save.
  3. **An `Origin` check** on anything that is not a plain GET, because a cross-site form
     post can carry cookies but cannot forge `Origin`.

The token is deliberately not a cookie. Cookies are attached by the browser to *every*
request to the origin, which is the property that makes cross-site requests dangerous;
a token the page must send explicitly is not.
"""
from __future__ import annotations

import logging
import mimetypes
import secrets
from pathlib import Path

from aiohttp import web

from . import sources

log = logging.getLogger("palintel.ui")

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765


def _json(payload, status: int = 200) -> web.Response:
    return web.json_response(payload, status=status,
                             headers={"Cache-Control": "no-store"})


@web.middleware
async def guard(request: web.Request, handler):
    """Token and origin, on everything except the loader page and its assets.

    The shell itself is served without a token so the URL you paste is one you can open;
    it carries the token to the browser and every API call presents it from there. Nothing
    sensitive is in the shell - it is markup and styling, and it renders empty without a
    token that works.
    """
    if request.path in ("/", "/favicon.ico") or request.path.startswith("/static/"):
        return await handler(request)

    token = request.app["token"]
    sent = (request.headers.get("X-PalIntel-Token")
            or request.query.get("token", ""))
    if not secrets.compare_digest(sent, token):
        return _json({"error": "bad or missing token"}, status=403)

    if request.method != "GET":
        # A cross-site POST cannot set Origin to ours. Same-origin fetches from the shell
        # always send it, so a missing Origin on a write is refused rather than trusted.
        origin = request.headers.get("Origin", "")
        allowed = {f"http://127.0.0.1:{request.app['port']}",
                   f"http://localhost:{request.app['port']}"}
        if origin not in allowed:
            return _json({"error": f"origin {origin!r} not allowed"}, status=403)

    return await handler(request)


async def index(request: web.Request) -> web.Response:
    """The shell. Rendered with the token inlined so the page can call the API."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    html = html.replace("%%TOKEN%%", request.app["token"])
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-store"})


async def static_file(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    path = STATIC / name
    # Resolved and re-checked rather than trusted: `..` in a path is the oldest bug there
    # is, and "it only listens on loopback" is not an answer to it.
    if not path.resolve().is_relative_to(STATIC.resolve()) or not path.is_file():
        raise web.HTTPNotFound()
    ctype, _ = mimetypes.guess_type(str(path))
    return web.Response(body=path.read_bytes(),
                        content_type=ctype or "application/octet-stream")


async def api_sessions(request: web.Request) -> web.Response:
    return _json([s.__dict__ for s in sources.list_sessions()])


async def api_session(request: web.Request) -> web.Response:
    detail = sources.session_detail(request.match_info["session"])
    if not detail:
        return _json({"error": "no such session"}, status=404)
    return _json(detail)


async def api_clip(request: web.Request) -> web.Response:
    path = sources.clip_path(request.match_info["session"], request.match_info["uid"])
    if path is None:
        raise web.HTTPNotFound()
    return web.Response(body=path.read_bytes(), content_type="audio/wav")


async def api_save(request: web.Request) -> web.Response:
    """A full save poll, including the multi-megabyte Level.sav walk. On request only."""
    return _json(sources.save_state(request.app["save_dir"]))


async def api_overview(request: web.Request) -> web.Response:
    """Everything the status panel needs except the save, which is slow and asked for
    separately so the page can paint before it arrives."""
    return _json({
        "spend": sources.spend_state(),
        "latency": sources.latency_state(),
        "sessions": len(sources.list_sessions()),
        # Named here rather than left implicit: a console that silently omits what it
        # cannot see would read as "the bot has no voice" rather than "ask the bot".
        "bot": {"reachable": False,
                "unavailable": ["voice", "receive counters", "router", "uptime"],
                "note": "process control and live bot state arrive in a later phase"},
    })


def build_app(token: str, port: int, save_dir: Path | None = None) -> web.Application:
    app = web.Application(middlewares=[guard])
    app["token"], app["port"], app["save_dir"] = token, port, save_dir
    app.add_routes([
        web.get("/", index),
        web.get("/static/{name}", static_file),
        web.get("/api/overview", api_overview),
        web.get("/api/save", api_save),
        web.get("/api/sessions", api_sessions),
        web.get("/api/sessions/{session}", api_session),
        web.get("/api/sessions/{session}/clip/{uid}", api_clip),
    ])
    return app


def serve(port: int = DEFAULT_PORT, save_dir: Path | None = None,
          open_browser: bool = True) -> None:
    token = secrets.token_urlsafe(24)
    app = build_app(token, port, save_dir)
    url = f"http://127.0.0.1:{port}/?token={token}"

    # Flushed, because this URL is the only way in and stdout is block-buffered whenever
    # it is not a terminal - so redirecting the console to a log file would hide the one
    # line you need to use it, until the process exits.
    print(f"\n  PalIntel console\n  {url}\n", flush=True)
    if open_browser:
        import threading
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    web.run_app(app, host="127.0.0.1", port=port, print=None)
