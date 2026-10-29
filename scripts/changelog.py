"""Read CHANGELOG.md for the release workflows.

The newest version is the first ``## X.Y.Z`` heading. Merging a pull request
into master releases that version if its tag does not exist yet, so every
pull request has to add a new section on top (see ``check``).

    python scripts/changelog.py version            # 1.1.0
    python scripts/changelog.py notes [VERSION]    # that section's text
    python scripts/changelog.py check --base REF [--no-release]
    python scripts/changelog.py consistent         # version.py == changelog

Standard library only, so the workflows need no install step.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = "CHANGELOG.md"
VERSION_FILE = "src/autoclicker/version.py"

HEADING = re.compile(r"^## \[?v?(\d+)\.(\d+)\.(\d+)\]?(?:\s.*)?$")
VERSION = re.compile(r'__version__\s*=\s*["\'](\d+\.\d+\.\d+)["\']')


def sections(text: str) -> list[tuple[str, str]]:
    """(version, body) for each release section, newest first."""
    found: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        match = HEADING.match(line.strip())
        if match:
            found.append((".".join(match.groups()), []))
        elif found:
            found[-1][1].append(line)
    return [(version, "\n".join(body).strip()) for version, body in found]


def key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def git_show(ref: str, path: str) -> str | None:
    """A file's text at ``ref``, or None if it does not exist there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=ROOT, capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else None


def tag_exists(version: str) -> bool:
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/v{version}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def file_version(text: str | None) -> str | None:
    match = VERSION.search(text or "")
    return match.group(1) if match else None


def latest(text: str | None) -> tuple[str, str] | None:
    found = sections(text or "")
    return found[0] if found else None


def check(base: str, no_release: bool) -> list[str]:
    """Why this branch cannot be merged into ``base``; empty means it can."""
    problems = []
    head = latest((ROOT / CHANGELOG).read_text(encoding="utf-8"))
    code_version = file_version((ROOT / VERSION_FILE).read_text(encoding="utf-8"))

    # Before CHANGELOG.md existed, version.py was the only record.
    base_top = latest(git_show(base, CHANGELOG))
    base_version = base_top[0] if base_top else file_version(git_show(base, VERSION_FILE))

    if head is None:
        return [f"{CHANGELOG} has no '## X.Y.Z' section."]
    version, notes = head

    if code_version != version:
        problems.append(
            f"{VERSION_FILE} says {code_version}, {CHANGELOG} says {version}: make them match."
        )

    if no_release:
        if base_version and key(version) != key(base_version):
            problems.append(
                f"Labelled 'no release', but {CHANGELOG} moved from {base_version} to "
                f"{version}. Remove the new section or the label."
            )
        return problems

    if not notes:
        problems.append(f"The {version} section in {CHANGELOG} is empty: say what changed.")
    if base_version and key(version) <= key(base_version):
        problems.append(
            f"Add a new section on top of {CHANGELOG}: {version} is not newer than "
            f"{base_version} on {base}. If another release merged first, update this "
            "branch and bump past it."
        )
    if tag_exists(version):
        problems.append(f"v{version} is already released: bump to a new version.")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version", help="print the newest version")
    notes = commands.add_parser("notes", help="print a version's release notes")
    notes.add_argument("version", nargs="?")
    checker = commands.add_parser("check", help="fail unless this branch adds a release")
    checker.add_argument("--base", required=True, help="the ref being merged into")
    checker.add_argument("--no-release", action="store_true")
    commands.add_parser("consistent", help="fail unless version.py matches")
    args = parser.parse_args(argv)

    found = sections((ROOT / CHANGELOG).read_text(encoding="utf-8"))
    if not found:
        print(f"{CHANGELOG} has no '## X.Y.Z' section.", file=sys.stderr)
        return 1

    if args.command == "version":
        print(found[0][0])
        return 0

    if args.command == "notes":
        wanted = args.version or found[0][0]
        for version, body in found:
            if version == wanted:
                print(body)
                return 0
        print(f"No {wanted} section in {CHANGELOG}.", file=sys.stderr)
        return 1

    if args.command == "consistent":
        code_version = file_version((ROOT / VERSION_FILE).read_text(encoding="utf-8"))
        if code_version != found[0][0]:
            print(f"{VERSION_FILE} says {code_version}, {CHANGELOG} says {found[0][0]}.", file=sys.stderr)
            return 1
        return 0

    problems = check(args.base, args.no_release)
    for problem in problems:
        print(f"::error file={CHANGELOG}::{problem}")
    if not problems:
        version = found[0][0]
        print("No release: changelog unchanged." if args.no_release else f"Merging releases v{version}.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
