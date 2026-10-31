"""The release script's reading of CHANGELOG.md."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "changelog", Path(__file__).resolve().parent.parent / "scripts" / "changelog.py"
)
changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog)

TEXT = """# Changelog

Intro text that belongs to no release.

## 1.10.0 - 2026-11-01

### Added
- Something new

## [v1.9.2]

Fixed a thing.
"""


def test_sections_newest_first_with_their_text():
    assert changelog.sections(TEXT) == [
        ("1.10.0", "### Added\n- Something new"),
        ("1.9.2", "Fixed a thing."),
    ]


def test_versions_compare_as_numbers_not_text():
    assert changelog.key("1.10.0") > changelog.key("1.9.2")


@pytest.mark.parametrize("heading", ["## Unreleased", "## 1.2", "### 1.2.3", "## 1.2.3-beta"])
def test_only_full_version_headings_count(heading):
    assert changelog.sections(f"{heading}\n\ntext\n") == []


def test_repository_changelog_matches_version_py():
    assert changelog.main(["consistent"]) == 0
