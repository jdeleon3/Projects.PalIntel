"""PalIntel - a Palworld assistant. See Docs/ for design.

Loads a repo-root .env at import so every entry point (CLI, bot, tests) sees the same
environment. Real environment variables win over .env values: an explicitly exported
key should always beat a file, and the reverse makes "why is it using the wrong key"
very hard to diagnose.
"""
from __future__ import annotations

__version__ = "0.1.0"


def _load_dotenv() -> None:
    from pathlib import Path

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


_load_dotenv()
