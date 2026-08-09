"""Configuration loading.

Values come from config.local.toml, overridden by environment variables. The file is
gitignored because it holds a bot token and a machine-specific save path; the
environment path exists so the token never has to touch disk at all if you would rather
it did not.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO / "config.local.toml"


class ConfigError(RuntimeError):
    pass


def _toml_help(path: Path, err: Exception) -> str:
    """Turn a TOML parse error into something actionable.

    Windows paths in double-quoted TOML are the overwhelmingly likely cause: "C:\\Users"
    fails because \\U opens a unicode escape. The raw parser error says "Invalid hex
    value", which points nowhere useful.
    """
    msg = [f"{path} is not valid TOML:", f"  {err}"]
    try:
        line_no = int(str(err).rsplit("at line ", 1)[1].split(",")[0])
        line = path.read_text(encoding="utf-8").splitlines()[line_no - 1]
        msg.append(f"  line {line_no}: {line.strip()}")
        if "\\" in line and '"' in line:
            msg += [
                "",
                "  A Windows path in double quotes: TOML reads \\U, \\a, \\t as escapes.",
                "  Use single quotes (a literal string) or forward slashes:",
                f"    {line.split('=')[0].strip()} = 'C:\\Users\\you\\...'",
                f"    {line.split('=')[0].strip()} = \"C:/Users/you/...\"",
            ]
    except (IndexError, ValueError):
        pass
    return "\n".join(msg)


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    channel_id: int
    listen_mode: str = "any"     # any | prefix | mention
    prefix: str = "?"


@dataclass(frozen=True)
class VoiceConfig:
    """Voice input. `enabled = false` runs the bot text-only.

    Input is the local microphone. Discord voice receive is blocked upstream by
    Discord's DAVE encryption (Pycord-Development/pycord#3139) - it connects, accepts a
    sink, and delivers no audio. Output is a Discord channel either way; only the input
    moved.

    `models` is a list because a pretrained model can run alongside a custom one during
    a transition - inference is CPU-bound at a realtime factor near 0.015, so the second
    model is close to free, and the highest score wins.
    """
    enabled: bool = False
    models: tuple[str, ...] = ("hey_pal",)
    threshold: float = 0.5
    device: int | str | None = None      # None = the system default input


@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    voice: VoiceConfig = field(default_factory=VoiceConfig)
    data_version: str = "1.0.2"
    save_dir: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_PATH
        raw: dict = {}
        if path.exists():
            try:
                raw = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as e:
                raise ConfigError(_toml_help(path, e)) from e
        elif not os.environ.get("PALINTEL_DISCORD_TOKEN"):
            raise ConfigError(
                f"No config at {path} and PALINTEL_DISCORD_TOKEN is unset.\n"
                f"  copy config.example.toml config.local.toml   and fill it in.")

        d = raw.get("discord", {})
        token = os.environ.get("PALINTEL_DISCORD_TOKEN", d.get("token", "")).strip()
        channel = int(os.environ.get("PALINTEL_CHANNEL_ID", d.get("channel_id", 0)))

        if not token:
            raise ConfigError("discord.token is empty - see config.example.toml")
        if not channel:
            raise ConfigError("discord.channel_id is 0 - see Docs/discord-setup.md")

        mode = d.get("listen_mode", "any")
        if mode not in ("any", "prefix", "mention"):
            raise ConfigError(f"listen_mode must be any|prefix|mention, got {mode!r}")

        v = raw.get("voice", {}) or {}
        if "channel_id" in v:
            raise ConfigError(
                "voice.channel_id is no longer used. Voice input is the local "
                "microphone: Discord voice receive is blocked by Discord's DAVE "
                "encryption (pycord#3139) and delivers no audio. Use "
                "voice.enabled = true, and voice.device to pick a specific mic.")
        models = tuple(v.get("models") or ("hey_pal",))
        device = v.get("device")

        save = (raw.get("game", {}) or {}).get("save_dir", "").strip()
        return cls(
            discord=DiscordConfig(token=token, channel_id=channel,
                                  listen_mode=mode, prefix=d.get("prefix", "?")),
            voice=VoiceConfig(enabled=bool(v.get("enabled", False)), models=models,
                              threshold=float(v.get("threshold", 0.5)),
                              device=device),
            data_version=os.environ.get(
                "PALINTEL_DATA_VERSION", (raw.get("data", {}) or {}).get("version", "1.0.2")),
            save_dir=Path(save) if save else None,
        )

    def redacted(self) -> dict:
        """Safe to log or print - never exposes the token."""
        t = self.discord.token
        return {
            "token": f"{t[:6]}...{t[-4:]} ({len(t)} chars)" if t else "(unset)",
            "channel_id": self.discord.channel_id,
            "listen_mode": self.discord.listen_mode,
            "voice": (f"mic, {', '.join(self.voice.models)}"
                      if self.voice.enabled else "(text only)"),
            "data_version": self.data_version,
            "save_dir": str(self.save_dir) if self.save_dir else "(none)",
        }
