"""Command line entry point."""

from __future__ import annotations

import argparse
import sys

from .config import LAST_SESSION, Config, config_dir, list_profiles
from .version import __version__

BACKGROUND_MESSAGE = (
    "Running in the background. Look for the icon in your system tray, or stop it\n"
    "with: autoclicker --stop-background"
)


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

    background = parser.add_argument_group("background mode")
    background.add_argument(
        "--background",
        action="store_true",
        help="run detached from this terminal, in the system tray, and exit",
    )
    background.add_argument(
        "--tray",
        action="store_true",
        help="run the tray process itself, in the foreground (--background spawns this)",
    )
    background.add_argument(
        "--start",
        action="store_true",
        help="with --background/--tray: begin clicking straight away",
    )
    background.add_argument(
        "--stay-online",
        action="store_true",
        help="with --background/--tray: begin staying online straight away",
    )
    background.add_argument(
        "--stop-background",
        action="store_true",
        help="stop the running background instance",
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
        from . import tray

        if tray.icon_available():
            tray_line = "Background mode has a system-tray icon here."
        else:
            tray_line = (
                "Background mode runs headless here (no tray icon): "
                f"{tray.UNAVAILABLE_REASON or 'pystray is not installed'}. "
                "Stop it with --stop-background."
            )
        if problem:
            print(problem, file=sys.stderr)
            print(lock_line, file=sys.stderr)
            print(tray_line, file=sys.stderr)
            return 1
        print("Input control is available.")
        print(lock_line)
        print(tray_line)
        return 0

    if args.stop_background:
        from . import tray

        print(tray.stop_background())
        return 0

    name = args.profile or LAST_SESSION
    if args.profile and args.profile not in list_profiles():
        print(f"No profile named '{args.profile}'.", file=sys.stderr)
        return 1

    if args.tray:
        from . import tray

        return tray.run(profile=name, click=args.start, keeper=args.stay_online)

    if args.background:
        from . import tray

        error = tray.launch_background(
            profile=name, click=args.start, keeper=args.stay_online
        )
        if error:
            print(error, file=sys.stderr)
            return 1
        print(BACKGROUND_MESSAGE)
        return 0

    from .tui import AutoClickerApp

    if AutoClickerApp(Config.load(name)).run() == "background":
        print(BACKGROUND_MESSAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
