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
from .pipeline import Pipeline, PlayerState, build_router
from .tools import Decline


def build(version: str, router: str = "auto", fast_path: bool = True,
          cues: str = "wide") -> Pipeline:
    from .config import RouterConfig

    kb = KnowledgeBase.load(version)
    return Pipeline(kb, build_router(kb, router,
                                     RouterConfig(fast_path=fast_path, cues=cues)))


def show(pipe: Pipeline, text: str, state: PlayerState, verbose: bool) -> None:
    outcome = pipe.handle(text, state)
    print()
    print("\n\n".join(c.to_text() for c in outcome.cards))
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
        # Follow-up resolution is the one thing here that depends on earlier turns, so a
        # wrong referent is only diagnosable if the memory is visible beside the answer.
        print(f"  memory: {pipe.memory.describe('local')}")
    print()


def _configured_save_dir() -> str:
    """`game.save_dir` out of config.local.toml, without loading the whole config.

    `Config.load` raises when there is no Discord token, and this harness exists
    precisely so the pipeline can be driven with no bot wiring at all - so reaching for
    it here would make `--save` depend on a credential it has no use for.
    """
    import tomllib
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "config.local.toml"
    if not path.exists():
        return ""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return ""
    return (raw.get("game", {}) or {}).get("save_dir", "").strip()


def _state(args) -> PlayerState:
    """Player state from a real save, from the flags, or empty.

    Q5's counter filter and the whole of Q6 need save state, and before this the harness
    had no way to supply any - so the two classes that most depend on it were the two
    that could only be exercised through Discord. `--level` and `--at` still win over the
    save, because they exist to reproduce a specific reading.
    """
    coords = tuple(float(v) for v in args.at.split(",")) if args.at else None
    if not (args.save or args.save_dir):
        return PlayerState(player_level=args.level, player_coords=coords)

    from pathlib import Path

    from .saves import SaveWatcher

    save_dir = args.save_dir or _configured_save_dir()
    if not save_dir:
        sys.exit("--save needs game.save_dir in config.local.toml, or pass --save-dir")
    watcher = SaveWatcher(Path(save_dir))
    if not watcher.poll():
        sys.exit(f"could not read a player save: {watcher.error}")
    # Synchronously, and it takes seconds. Fine here and emphatically not on the bot's
    # query path, which is why the bot polls it on a timer instead.
    watcher.poll_roster()
    if watcher.roster is None:
        print(f"  (no roster: {watcher.roster_error})", file=sys.stderr)
    snapshot = watcher.snapshot
    print(f"  save: {watcher.describe()}{'' if watcher.roster is None else ', ' + watcher.describe_roster()}"
          f", {len(snapshot.technologies)} technologies", file=sys.stderr)
    return PlayerState(
        player_level=args.level,
        player_coords=coords or watcher.player_coords(),
        owned_species=watcher.roster,
        tech=watcher.player_tech(),
        base_camps=watcher.base_camps,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("utterance", nargs="*", help="one-shot query; omit for interactive")
    ap.add_argument("--version", default="1.0.2")
    ap.add_argument("--router", default="auto", choices=["auto", "claude", "stub"],
                    help="auto falls back to the stub when no credential resolves")
    # The two fast-path flags, so a suspect answer can be re-asked straight against the
    # model without editing config and restarting the bot.
    ap.add_argument("--no-fast-path", action="store_true",
                    help="always ask the model, even when the stub could answer")
    ap.add_argument("--cues", default="wide",
                    choices=["standard", "proximity", "wide"],
                    help="how eagerly the fast path claims a query (default wide)")
    ap.add_argument("--status", action="store_true", help="print loaded data and exit")
    ap.add_argument("--level", type=int, help="simulate player level")
    ap.add_argument("--at", help="simulate position as 'x,y'")
    # A flag rather than an optional-value option: `--save` with `nargs="?"` swallows the
    # first word of the utterance, because the positional is `nargs="*"` and argparse
    # resolves that ambiguity the wrong way round for this harness.
    ap.add_argument("--save", action="store_true",
                    help="read config.local.toml's save for position, roster and "
                         "technologies")
    ap.add_argument("--save-dir", metavar="DIR",
                    help="read this save directory instead of the configured one")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="show routing and candidate ranking")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    try:
        pipe = build(args.version, args.router,
                     fast_path=not args.no_fast_path, cues=args.cues)
    except FileNotFoundError as e:
        sys.exit(f"missing data: {e}\nRun tools/ingest/ to build it.")
    except RuntimeError as e:
        # Missing credential / missing SDK: actionable, not a stack trace.
        sys.exit(str(e))

    if args.status:
        print(json.dumps(pipe.kb.summary(), indent=2))
        return

    state = _state(args)

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
        if text.lower() in ("/reset", "reset"):
            pipe.memory.forget()
            print("  (forgotten)\n")
            continue
        if text:
            show(pipe, text, state, args.verbose)


if __name__ == "__main__":
    main()
