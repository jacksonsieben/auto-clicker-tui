#!/usr/bin/env python3
"""Build a standalone binary for the platform this runs on.

Usage:
    python scripts/build.py            # one-file binary
    python scripts/build.py --onedir   # folder build, zipped

The one-file build unpacks itself into a temporary directory at start-up, which
some corporate application-control policies forbid. The ``--onedir`` build has
no such step, so it is the fallback we ship alongside it for Windows.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DIST = ROOT / "dist"
BUILD = ROOT / "build"

PLATFORM_NAMES = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}

# Imported through pynput's backend indirection, so PyInstaller cannot see them.
HIDDEN_IMPORTS = {
    "windows": ["pynput.keyboard._win32", "pynput.mouse._win32"],
    "macos": ["pynput.keyboard._darwin", "pynput.mouse._darwin"],
    "linux": ["pynput.keyboard._xorg", "pynput.mouse._xorg"],
}

# Textual imports its widgets lazily through a module __getattr__, so static
# analysis finds none of them; it also ships .tcss files next to the code.
COLLECT_ALL = ["textual"]

# Nothing here is used, and tkinter in particular is large and often absent.
EXCLUDES = ["tkinter", "PIL", "numpy", "pytest", "setuptools", "pip"]


def target() -> str:
    name = PLATFORM_NAMES.get(platform.system())
    if name is None:
        sys.exit(f"Unsupported platform: {platform.system()}")
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onedir",
        action="store_true",
        help="build a folder instead of a single file, and zip it",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="override the output name (default: autoclicker-<platform>)",
    )
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        sys.exit("PyInstaller is missing. Install it with: pip install '.[build]'")

    os_name = target()
    name = args.name or ("autoclicker" if args.onedir else f"autoclicker-{os_name}")

    # Clear only this target's leftovers: the Windows job builds one-file and
    # one-folder in turn, and must not delete the artifact it just produced.
    shutil.rmtree(BUILD / name, ignore_errors=True)
    shutil.rmtree(DIST / name, ignore_errors=True)
    for stale in (DIST / f"{name}.exe", DIST / name):
        if stale.is_file():
            stale.unlink()

    separator = ";" if os_name == "windows" else ":"
    css = SRC / "autoclicker" / "tui.tcss"

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir" if args.onedir else "--onefile",
        "--console",
        "--name",
        name,
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        "--paths",
        str(SRC),
        # Textual resolves CSS_PATH next to tui.py, so the sheet has to land in
        # the package directory inside the bundle.
        "--add-data",
        f"{css}{separator}autoclicker",
    ]
    for module in HIDDEN_IMPORTS[os_name]:
        cmd += ["--hidden-import", module]
    for package in COLLECT_ALL:
        cmd += ["--collect-all", package]
    for module in EXCLUDES:
        cmd += ["--exclude-module", module]
    cmd.append(str(ROOT / "scripts" / "entry.py"))

    print(" ".join(cmd), flush=True)
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    if args.onedir:
        folder = DIST / name
        archive = DIST / f"autoclicker-{os_name}-folder"
        shutil.make_archive(str(archive), "zip", root_dir=DIST, base_dir=name)
        print(f"\nBuilt {folder}{os.sep} and {archive}.zip")
    else:
        binary = DIST / (f"{name}.exe" if os_name == "windows" else name)
        size = binary.stat().st_size / (1024 * 1024)
        print(f"\nBuilt {binary} ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
