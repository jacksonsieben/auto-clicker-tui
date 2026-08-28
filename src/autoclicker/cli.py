"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from .config import LAST_SESSION, Config, config_dir, list_profiles
from .version import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="autoclicker",
        description="A cross-platform auto clicker that runs in your terminal.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "-p",
        "--profile",
        metavar="NAME",
        help="start with a saved profile instead of the last session",
    )
    parser.add_argument(
        "--list-profiles", action="store_true", help="list saved profiles and exit"
    )
    parser.add_argument(
        "--config-dir", action="store_true", help="print the config directory and exit"
    )
    parser.add_argument(
        "--check", action="store_true", help="report whether input control works here"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.config_dir:
        print(config_dir())
        return 0

    if args.list_profiles:
        names = list_profiles()
        print("\n".join(names) if names else "No saved profiles.")
        return 0

    if args.check:
        from . import backend, session

        problem = backend.diagnose()
        support = session.lock_support()
        if support:
            lock_line = f"Screen locks are detected via {support}."
        else:
            lock_line = (
                "Screen locks cannot be detected here; only a suspend will stop a run."
            )
        if problem:
            print(problem, file=sys.stderr)
            print(lock_line, file=sys.stderr)
            return 1
        print("Input control is available.")
        print(lock_line)
        return 0

    name = args.profile or LAST_SESSION
    if args.profile and args.profile not in list_profiles():
        print(f"No profile named '{args.profile}'.", file=sys.stderr)
        return 1

    from .tui import AutoClickerApp

    AutoClickerApp(Config.load(name)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
