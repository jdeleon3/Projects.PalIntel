"""Starting and stopping the bot from the console.

**The bot is not owned by the console.** It is a sibling that happens to have been
launched by it: it survives the console closing, and it can equally have been started from
a terminal. So every decision here is made from the heartbeat (`botstate`) rather than
from a handle this process is holding - a handle answers "did I start one", and the
question that matters is "is one running".

That distinction is the whole reason this is careful. Two bots on one Discord token both
connect and both answer, so the only symptom is every question answered twice, and the
console is the thing most likely to cause it: close it, reopen it, press Start.

Startup failures are the case this exists for. `Config.load` raising means the bot exits
immediately, and a console that showed "not running" without saying why would be useless
in exactly the situation that motivated building it - so the child's output is captured
and the tail comes back with the failure.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from .. import botstate

log = logging.getLogger("palintel.ui.process")

REPO = Path(__file__).resolve().parents[2]
LOG_PATH = REPO / "data" / "bot.log"

# How long to wait for a started bot to either publish a heartbeat or exit. Loading the
# knowledge base, the lexicon and the datasets takes seconds before Discord is even dialled.
START_TIMEOUT = 45.0
# After asking politely, how long before insisting. py-cord needs a moment to close its
# gateway connection cleanly; killing it immediately leaves Discord thinking the bot is
# still present for a while, which shows up as the next start being ignored.
STOP_GRACE = 12.0


def alive(pid: int) -> bool:
    """Is this process still running?

    **Asked directly, because the obvious proxies are both wrong.** Waiting for the
    heartbeat to go stale cannot work: `STOP_GRACE` is shorter than `STALE_SECONDS`, by
    design — a bot briefly blocked must not read as dead — so a stop that polled for
    staleness reported failure on a process it had successfully killed. That is the worst
    shape of wrong answer here, because it invites you to kill it again.

    And `os.kill(pid, 0)` is not the portable liveness check it looks like: on Windows,
    Python maps every signal except CTRL_C/CTRL_BREAK onto `TerminateProcess`, so the
    idiom that merely *probes* on POSIX would **kill the process** here.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


class Supervisor:
    """Runs at most one bot, and knows about ones it did not run."""

    def __init__(self):
        self._child: subprocess.Popen | None = None

    # --- state ----------------------------------------------------------------

    def status(self) -> dict:
        """What the console shows. Heartbeat first, own child second.

        A bot this console did not start is reported as running with `adopted` set, and it
        can be stopped - because the alternative is a Start button that refuses forever
        with no way to resolve it from here.
        """
        state = botstate.read()
        mine = self._child is not None and self._child.poll() is None
        out = dict(state)
        out["ours"] = mine
        out["adopted"] = bool(state.get("running")) and not mine
        if not state.get("running") and mine:
            # Started, not yet beating. Distinguished from "running" so the UI can say
            # "starting…" rather than flickering between states.
            out["starting"] = True
        out["log"] = str(LOG_PATH)
        return out

    def tail(self, lines: int = 40) -> str:
        """The end of the bot's output. What a failed start actually said."""
        if not LOG_PATH.exists():
            return ""
        try:
            text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"(could not read {LOG_PATH}: {e})"
        return "\n".join(text.splitlines()[-lines:])

    # --- control --------------------------------------------------------------

    def start(self) -> dict:
        """Launch the bot, unless one is already running.

        The guard is the heartbeat, so it holds against a bot started from a terminal, a
        bot left over from a previous console, and a bot started by a second console.
        """
        state = botstate.read()
        if state.get("running"):
            return {"ok": False,
                    "error": f"a bot is already running (pid {state.get('pid')}). "
                             f"Stop it first — two on one token answer everything twice."}
        if self._child is not None and self._child.poll() is None:
            return {"ok": False, "error": "a bot was just started and has not settled yet"}

        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Truncated per start: this log exists to answer "why did THIS start fail", and an
        # append-only file makes the reader scroll past every previous attempt to find it.
        handle = LOG_PATH.open("w", encoding="utf-8")
        try:
            self._child = subprocess.Popen(
                [sys.executable, "-m", "palintel.bot"],
                cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # Its own process group, so the console's own Ctrl-C does not travel to a
                # bot that is meant to outlive it.
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP
                               if os.name == "nt" else 0),
                start_new_session=(os.name != "nt"),
            )
        except Exception as e:
            handle.close()
            return {"ok": False, "error": f"could not launch: {type(e).__name__}: {e}"}

        log.info("started the bot as pid %s", self._child.pid)
        return {"ok": True, "pid": self._child.pid,
                "note": "starting — it loads the datasets before dialling Discord"}

    def wait_started(self, timeout: float = START_TIMEOUT) -> dict:
        """Block until the bot beats or dies. Called by the API so the page can say which.

        The two failure shapes are different and both matter: an immediate exit is a
        config or credential problem and the log says exactly what, while a timeout with
        the process still alive is a bot that is running and not publishing - which is
        worth reporting as itself rather than as a failure.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if botstate.read().get("running"):
                return {"ok": True, "note": "running"}
            if self._child is not None and self._child.poll() is not None:
                return {"ok": False,
                        "error": f"the bot exited immediately (code "
                                 f"{self._child.returncode})",
                        "log": self.tail()}
            time.sleep(0.4)
        return {"ok": False,
                "error": f"no heartbeat after {timeout:.0f}s — it may still be loading",
                "log": self.tail()}

    def stop(self) -> dict:
        """Ask the bot to exit, then insist.

        Works on a bot this console did not start, by pid from the heartbeat. That is the
        point: a Start button that refuses because of an orphan, with no way to clear the
        orphan, is worse than no button.
        """
        state = botstate.read()
        pid = state.get("pid")
        child = self._child

        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=STOP_GRACE)
            except subprocess.TimeoutExpired:
                log.warning("the bot did not exit in %.0fs; killing", STOP_GRACE)
                child.kill()
                child.wait(timeout=5)
            self._child = None
            botstate.clear()
            return {"ok": True, "note": "stopped"}

        if not state.get("running") or pid is None:
            return {"ok": False, "error": "no bot is running"}

        # Adopted: not our child, so no handle to wait on. Signal by pid and confirm by
        # the heartbeat going stale rather than by a return code we cannot collect.
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            else:
                os.kill(int(pid), 15)
        except Exception as e:
            return {"ok": False, "error": f"could not stop pid {pid}: {e}"}

        # Confirmed by asking whether the PROCESS is gone, not by waiting for its
        # heartbeat to go stale - see `alive`. The staleness window is deliberately longer
        # than this grace period, so the old check could only ever time out.
        deadline = time.monotonic() + STOP_GRACE
        while time.monotonic() < deadline:
            if not alive(int(pid)):
                # Ours to clear: the process that owns this file is gone, and leaving it
                # would make the console refuse to start a bot for STALE_SECONDS.
                botstate.clear()
                return {"ok": True, "note": f"stopped pid {pid}"}
            time.sleep(0.3)
        return {"ok": False,
                "error": f"pid {pid} was signalled and is still running"}

    def restart(self) -> dict:
        stopped = self.stop()
        # A restart when nothing is running is a start, not an error - which is what
        # anyone pressing it while looking at a crashed bot means by it.
        if not stopped["ok"] and "no bot is running" not in stopped.get("error", ""):
            return stopped
        started = self.start()
        if not started["ok"]:
            return started
        return self.wait_started()
