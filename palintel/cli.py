"""Local harness — drive the pipeline without Discord.

Exists so the pipeline can be built, measured, and debugged before any bot wiring. The
Discord layer becomes a thin adapter over the same Pipeline.

    python -m palintel.cli                      interactive
    python -m palintel.cli "where's the coal"    single query
    python -m palintel.cli --status              loaded data summary
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .knowledge import KnowledgeBase
from .pipeline import Pipeline, PlayerState
from .routing import StubRouter
from .tools import Decline


def build(version: str) -> Pipeline:
    kb = KnowledgeBase.load(version)
    locatable = {n.resource for n in kb.nodes}
    return Pipeline(kb, StubRouter(kb.lexicon, locatable))


def show(pipe: Pipeline, text: str, state: PlayerState, verbose: bool) -> None:
    outcome = pipe.handle(text, state)
    print()
    print(outcome.card.to_text())
    if verbose:
        print("\n--- diagnosis ---")
        if isinstance(outcome.call, Decline):
            print(f"  routed: DECLINE ({outcome.call.reason})")
        else:
            print(f"  routed: {outcome.call.name}({outcome.call.args})")
            print(f"  why:    {outcome.call.rationale}")
        print("  candidates:")
        for c in outcome.candidates:
            print(f"    {c.score:.2f}  {c.canonical:<20} <- {c.matched_text!r}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("utterance", nargs="*", help="one-shot query; omit for interactive")
    ap.add_argument("--version", default="1.0.2")
    ap.add_argument("--status", action="store_true", help="print loaded data and exit")
    ap.add_argument("--level", type=int, help="simulate player level")
    ap.add_argument("--at", help="simulate position as 'x,y'")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show routing and candidate ranking")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        pipe = build(args.version)
    except FileNotFoundError as e:
        sys.exit(f"missing data: {e}\nRun tools/ingest/ to build it.")

    if args.status:
        print(json.dumps(pipe.kb.summary(), indent=2))
        return

    state = PlayerState(
        player_level=args.level,
        base_coords=tuple(float(v) for v in args.at.split(",")) if args.at else None,
    )

    if args.utterance:
        show(pipe, " ".join(args.utterance), state, args.verbose)
        return

    s = pipe.kb.summary()
    print(f"PalIntel · Palworld {s['game_version']} · "
          f"{s['node_clusters']} clusters · router={pipe.router.name}")
    print("Ask something, or Ctrl-C to quit.\n")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text:
            show(pipe, text, state, args.verbose)


if __name__ == "__main__":
    main()
