"""Reading and writing `config.local.toml` from the console, safely.

Three constraints shape all of this, and each of them rules out the obvious approach.

**The token must never reach the browser.** `config.local.toml` holds a Discord bot token,
which is a credential that can be used from anywhere. Nothing here reads it, returns it, or
round-trips it: the editable surface is a whitelist (`FIELDS`), and everything outside that
whitelist is not so much preserved as never touched.

**The comments are documentation and must survive.** This project's config file explains
*why* each flag exists - the TOML-escape trap, why `mic` is the default source, what
`balance_usd = 0` means. Serialising a parsed dict back out would delete all of it, so
writes are surgical line edits: find the line for one key in one section and replace the
value on it. Nothing else in the file moves.

**A bad write is a bot that will not start**, and you would be fixing it in a text editor -
which is exactly what the console exists to avoid. So a write is validated by loading the
candidate through `Config.load` before it replaces anything, and the previous file is kept.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("palintel.ui.config")

REPO = Path(__file__).resolve().parents[2]
DEFAULT_PATH = REPO / "config.local.toml"


@dataclass(frozen=True)
class Field:
    """One editable setting. `section` and `key` locate it in the TOML."""
    section: str
    key: str
    kind: str                       # bool | int | float | str | choice
    label: str
    help: str = ""
    choices: tuple[str, ...] = ()
    # Shown but not editable - a value you need to see to understand the others.
    readonly: bool = False


# **The whitelist IS the security boundary.** `discord.token` is deliberately absent and
# must stay absent: adding it here would send a credential to a browser.
FIELDS: tuple[Field, ...] = (
    Field("output", "medium", "choice", "Output medium",
          "`discord` posts into a channel; `local` writes to this machine's own Chat "
          "tab and needs no Discord account at all — see ADR-0018. The bot reads this "
          "at startup, same as everything else here: switching it takes a restart.",
          choices=("discord", "local")),
    Field("output", "poll_ms", "int", "Console poll (ms)",
          "How often this console tails the Chat tab's event log for new messages. "
          "Lower feels snappier and costs more file reads; a guess, not a measurement."),
    Field("output", "inbox_poll_ms", "int", "Bot poll (ms)",
          "How often the bot checks for a newly typed message, in local mode. This is "
          "the gap a player actually notices — between pressing enter and anything "
          "happening at all."),

    Field("discord", "channel_id", "int", "Text channel",
          "Where the bot listens and posts. Right-click the channel → Copy Channel ID."),
    Field("discord", "listen_mode", "choice", "Listen mode",
          "`any` answers every message — fine for a dedicated channel.",
          choices=("any", "prefix", "mention")),
    Field("discord", "prefix", "str", "Prefix",
          "Only used when listen mode is `prefix`."),

    Field("voice", "enabled", "bool", "Voice input",
          "Off runs the bot text-only. Everything else still works."),
    Field("voice", "source", "choice", "Voice source",
          "`mic` is the default deliberately: Discord receive fails by going quietly "
          "deaf, which ADR-0004 calls the worst kind, and a regression there should cost "
          "party voice rather than all voice.",
          choices=("mic", "discord")),
    Field("voice", "channel_id", "int", "Voice channel",
          "The VOICE channel id, not the text one. Required when source is `discord`; "
          "a text id there connects to nothing and the bot simply never hears anything."),
    Field("voice", "threshold", "float", "Wake threshold",
          "0.1 is deliberately low: a false negative is a silent failure (ADR-0004). "
          "The cost is the occasional false positive from a TV in the room."),
    Field("voice", "speaker", "str", "Attribute mic to",
          "Who the microphone belongs to — the mic cannot say. Unset it stays “voice”, "
          "and spoken questions cannot be followed up in text. Ignored on the Discord "
          "source, where every packet names its member."),

    Field("router", "fast_path", "bool", "Fast path",
          "Answer deterministically when the phrasing is unambiguous. Off restores "
          "model-only routing exactly, and roughly doubles the median latency."),
    Field("router", "cues", "choice", "Cue width",
          "How eagerly the deterministic router claims a query. `wide` measured best: "
          "14/18 Q1 and 43/49 Q2 with zero wrong.",
          choices=("standard", "proximity", "wide")),
    Field("router", "unified", "bool", "One tool",
          "One `answer_query` tool rather than one per class. Accuracy-neutral "
          "(McNemar p = 0.45) and 21% faster at the median."),

    Field("cards", "maps", "bool", "Map crops",
          "Needs data/<version>/assets/. Arrives as a second round trip after the text "
          "card, so it cannot move the graded latency."),
    Field("cards", "icons", "bool", "Pal icons", ""),

    Field("capture", "enabled", "bool", "Capture clips",
          "Keeps the WAV the pipeline already wrote, plus a log line. **Records whatever "
          "is near the microphone, including other people in the room.**"),
    Field("capture", "feedback", "bool", "Feedback buttons",
          "Labelling controls under every answer card. They ride in the same send() call, "
          "so they cost nothing on the graded path."),

    Field("cost", "enabled", "bool", "Log spend", ""),
    Field("cost", "balance_usd", "float", "Prepaid balance",
          "What you actually loaded onto the key. 0 means nothing is deducted or warned "
          "about — and a depleted balance arrives as HTTP 429, which every 429 turns into "
          "a Decline. A run was once read as a 13-point router regression before anyone "
          "checked."),
    Field("cost", "warn_below_usd", "float", "Warn below", ""),

    Field("game", "save_dir", "str", "Save directory",
          "Leave empty to follow whichever world is being played — the root is derivable "
          "and every world names itself. Set it only to pin one."),
    Field("data", "version", "str", "Dataset version",
          "Which extracted dataset under data/ to load.", readonly=True),
)


_SECTION = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


def _key_line(line: str) -> str | None:
    """The bare key a `key = value` line assigns, or None."""
    m = re.match(r"^\s*([A-Za-z0-9_-]+)\s*=", line)
    return m.group(1) if m else None


def _format(value: Any, kind: str) -> str:
    """A TOML literal.

    Strings are SINGLE-quoted, which is the whole reason this function exists rather than
    an f-string: TOML treats single quotes as a literal string, so a Windows path survives
    as typed. Double quotes make `C:\\Users` fail to parse because `\\U` opens a unicode
    escape - the trap `config._toml_help` was written to explain.
    """
    if kind == "bool":
        return "true" if value else "false"
    if kind == "int":
        return str(int(value))
    if kind == "float":
        # Always with a decimal point. `5` is a TOML *integer*, and while `Config.load`
        # coerces it, a float field that reads as an int in the file is a small lie to
        # whoever opens it next.
        f = float(value)
        return f"{f:.1f}" if f == int(f) and abs(f) < 1e15 else repr(f)
    text = "" if value is None else str(value)
    if "'" in text:
        # A literal string cannot contain a single quote. Escape into a basic string and
        # escape the backslashes with it, or the path trap comes back the other way round.
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f"'{text}'"


def set_values(text: str, updates: dict[tuple[str, str], Any],
               kinds: dict[tuple[str, str], str]) -> str:
    """Replace values in TOML text, in place, touching nothing else.

    Line-oriented on purpose - see the module docstring. A key that is present has its
    value rewritten where it sits; a key that is missing is inserted at the end of its
    section; a section that is missing is appended. Comments, ordering, blank lines and
    every key outside `updates` come through untouched.
    """
    lines = text.splitlines()
    out: list[str] = []
    section = ""
    seen: set[tuple[str, str]] = set()
    # Where each section's last line sits in `out`, so a missing key can be inserted into
    # the right block rather than appended to the end of the file under the wrong header.
    section_end: dict[str, int] = {}

    for line in lines:
        m = _SECTION.match(line)
        if m:
            section = m.group(1).strip()
        else:
            key = _key_line(line)
            if key is not None and (section, key) in updates:
                target = (section, key)
                value = _format(updates[target], kinds.get(target, "str"))
                # Keep any trailing comment on the line: it explains the setting.
                comment = ""
                after = line.split("=", 1)[1]
                hash_at = after.find("#")
                if hash_at >= 0 and after.count("'") % 2 == 0 and after.count('"') % 2 == 0:
                    comment = "  " + after[hash_at:].strip()
                indent = line[:len(line) - len(line.lstrip())]
                line = f"{indent}{key} = {value}{comment}"
                seen.add(target)
        out.append(line)
        if line.strip() or _SECTION.match(line):
            section_end[section] = len(out)

    for (sec, key), value in updates.items():
        if (sec, key) in seen:
            continue
        rendered = f"{key} = {_format(value, kinds.get((sec, key), 'str'))}"
        if sec in section_end:
            out.insert(section_end[sec], rendered)
            for s, at in section_end.items():
                if at > section_end[sec]:
                    section_end[s] = at + 1
            section_end[sec] += 1
        else:
            out += ["", f"[{sec}]", rendered]
            section_end[sec] = len(out)

    return "\n".join(out) + "\n"


def read(path: Path = DEFAULT_PATH) -> dict[str, Any]:
    """The editable settings and their current values. **Never the token.**"""
    import tomllib

    raw: dict = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            return {"ok": False, "error": str(e), "path": str(path), "fields": []}

    fields = []
    for f in FIELDS:
        value = (raw.get(f.section) or {}).get(f.key)
        fields.append({
            "section": f.section, "key": f.key, "kind": f.kind, "label": f.label,
            "help": f.help, "choices": list(f.choices), "readonly": f.readonly,
            "value": value,
        })
    token = (raw.get("discord") or {}).get("token") or ""
    return {
        "ok": True,
        "path": str(path),
        "exists": path.exists(),
        "fields": fields,
        # **Length only.** `Config.redacted()` shows `token[:6]…token[-4:]`, which is fine
        # for a local log and is more than a browser needs: the leading characters are the
        # bot's own id and the point here is only "is one configured". A test asserting
        # the secret never leaves this function caught the copied version doing it.
        "token_set": bool(token),
        "token_hint": f"configured, {len(token)} characters" if token else "(unset)",
    }


def write(updates: dict[str, Any], path: Path = DEFAULT_PATH) -> dict[str, Any]:
    """Apply changes, but only if the result actually loads.

    The order is the point: render the candidate, load it through `Config.load` in a temp
    file, and replace the real file only once that succeeded. A config the bot cannot parse
    never reaches disk, so the console cannot lock you out of itself.
    """
    from ..config import Config, ConfigError

    by_name = {f"{f.section}.{f.key}": f for f in FIELDS}
    typed: dict[tuple[str, str], Any] = {}
    kinds: dict[tuple[str, str], str] = {}
    for name, value in updates.items():
        f = by_name.get(name)
        if f is None:
            # Anything not on the whitelist - `discord.token` above all - is refused
            # rather than ignored, so a caller cannot discover what is writable by trying.
            return {"ok": False, "error": f"{name!r} is not an editable setting"}
        if f.readonly:
            return {"ok": False, "error": f"{name!r} is not editable"}
        try:
            if f.kind == "bool":
                typed[(f.section, f.key)] = bool(value)
            elif f.kind == "int":
                typed[(f.section, f.key)] = int(value)
            elif f.kind == "float":
                typed[(f.section, f.key)] = float(value)
            elif f.kind == "choice":
                if str(value) not in f.choices:
                    return {"ok": False,
                            "error": f"{name}: {value!r} is not one of {f.choices}"}
                typed[(f.section, f.key)] = str(value)
            else:
                typed[(f.section, f.key)] = str(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": f"{name}: {value!r} is not a {f.kind}"}
        kinds[(f.section, f.key)] = f.kind

    if not typed:
        return {"ok": True, "changed": 0, "note": "nothing to change"}

    original = path.read_text(encoding="utf-8") if path.exists() else ""
    candidate = set_values(original, typed, kinds)

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "config.local.toml"
        probe.write_text(candidate, encoding="utf-8")
        try:
            Config.load(probe)
        except ConfigError as e:
            return {"ok": False, "error": f"the bot would refuse this config:\n{e}"}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    if path.exists():
        # One generation back. Enough to undo a mistake, and it does not accumulate a
        # directory of backups nobody reads.
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    path.write_text(candidate, encoding="utf-8")
    log.info("config: wrote %d change(s) to %s", len(typed), path)
    return {"ok": True, "changed": len(typed),
            "note": "the bot reads this at startup — restart it to apply"}
