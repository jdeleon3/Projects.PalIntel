"""Configuration loading.

Values come from config.local.toml, overridden by environment variables. The file is
gitignored because it holds a bot token and a machine-specific save path; the
environment path exists so the token never has to touch disk at all if you would rather
it did not.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO / "config.local.toml"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    channel_id: int
    listen_mode: str = "any"     # any | prefix | mention
    prefix: str = "?"


@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    data_version: str = "1.0.2"
    save_dir: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or DEFAULT_PATH
        raw: dict = {}
        if path.exists():
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
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

        save = (raw.get("game", {}) or {}).get("save_dir", "").strip()
        return cls(
            discord=DiscordConfig(token=token, channel_id=channel,
                                  listen_mode=mode, prefix=d.get("prefix", "?")),
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
            "data_version": self.data_version,
            "save_dir": str(self.save_dir) if self.save_dir else "(none)",
        }
