"""Thin wrapper around pynput.

pynput touches the OS input stack at import time, which fails hard on
headless machines and on Wayland sessions.  Everything here degrades to
``AVAILABLE = False`` with a human-readable ``UNAVAILABLE_REASON`` so the
TUI can still start and explain itself instead of dying on a traceback.
"""

from __future__ import annotations

import os
import sys

AVAILABLE = False
UNAVAILABLE_REASON = ""

mouse = None
keyboard = None

try:  # pragma: no cover - depends on the host session
    from pynput import keyboard as keyboard  # type: ignore  # noqa: F811
    from pynput import mouse as mouse  # type: ignore  # noqa: F811

    AVAILABLE = True
except Exception as exc:  # pragma: no cover
    UNAVAILABLE_REASON = str(exc) or exc.__class__.__name__


def _wayland_warning() -> str:
    return (
        "This looks like a Wayland session. Wayland blocks applications from "
        "synthesizing mouse clicks, so the clicker cannot work here. Log in to "
        "an X11/Xorg session instead."
    )


def diagnose() -> str:
    """Return an empty string when input control should work, else why not."""
    if not AVAILABLE:
        return (
            f"Input backend unavailable: {UNAVAILABLE_REASON}\n"
            "Install the platform extras, or run inside a graphical session."
        )

    if sys.platform.startswith("linux"):
        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return _wayland_warning()
        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            return _wayland_warning()
        if not os.environ.get("DISPLAY"):
            return (
                "No X11 DISPLAY found. Start the app from inside a graphical "
                "session so it can control the mouse."
            )
    return ""


def button_for(name: str):
    """Map a config button name onto a pynput Button."""
    if mouse is None:
        raise RuntimeError("Input backend unavailable")
    return {
        "left": mouse.Button.left,
        "right": mouse.Button.right,
        "middle": mouse.Button.middle,
    }.get(name, mouse.Button.left)


# Keys the presence keeper can tap. F15 is the safest: real keyboards rarely
# have it, so nothing is bound to it, but the OS still counts it as activity.
KEEPER_KEYS = ("f15", "scroll_lock", "shift")


def key_for(name: str):
    """Map a keeper key name onto a pynput Key, falling back to shift."""
    if keyboard is None:
        raise RuntimeError("Input backend unavailable")
    key = getattr(keyboard.Key, name, None)
    if key is None:
        # f15 and scroll_lock are missing on some platforms/backends.
        key = getattr(keyboard.Key, "shift", None)
    if key is None:
        raise RuntimeError(f"No usable key for {name!r}")
    return key
