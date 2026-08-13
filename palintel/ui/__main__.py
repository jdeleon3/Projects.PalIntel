"""    python -m palintel.ui

The console. Runs independently of the bot on purpose - see `sources`: the job it must do
best is the one where the bot is not running, because that includes fixing whatever
stopped it starting.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .server import DEFAULT_PORT, serve


def main() -> None:
    ap = argparse.ArgumentParser(description="PalIntel console")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--save-dir", type=Path, default=None,
                    help="pin a world; omit to follow whichever one is being played")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    serve(port=args.port, save_dir=args.save_dir, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
