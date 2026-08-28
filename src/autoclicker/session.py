"""Noticing that the session went away: screen locked, or machine suspended.

Both checks are deliberately dependency-free.  Lock detection is per-platform
and best-effort -- when a machine offers no way to ask, :func:`lock_support`
says so and the caller can tell the user rather than pretend.  Suspend
detection is portable and needs no platform support at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

LOCKED = "locked"
SUSPENDED = "suspended"

_UNKNOWN = object()
_backend = _UNKNOWN  # resolved on first use


def _clean_env() -> dict[str, str]:
    """The environment to run system helpers under.

    A PyInstaller bundle points the loader at its own unpacked libraries, which
    makes system binaries like ``loginctl`` fail to start.  PyInstaller saves
    whatever the variables held before, under ``*_ORIG``; restore those.
    """
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        return env
    for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LD_PRELOAD"):
        original = env.pop(f"{name}_ORIG", None)
        if original:
            env[name] = original
        else:
            env.pop(name, None)
    return env


def _run(cmd: list[str], timeout: float = 1.5) -> str | None:
    """Run a helper command, returning its stdout or None if it failed."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env=_clean_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


# -- Windows -------------------------------------------------------------


def _windows_probe():
    """The input desktop cannot be opened while the secure/lock desktop is up."""
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    DESKTOP_SWITCHDESKTOP = 0x0100

    def locked() -> bool | None:
        handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
        if not handle:
            return True
        user32.CloseDesktop(handle)
        return False

    locked()  # fail here, at probe time, if the call is unavailable
    return locked, "Windows desktop state"


# -- macOS ---------------------------------------------------------------


def _macos_quartz_probe():
    from Quartz import CGSessionCopyCurrentDictionary  # type: ignore

    def locked() -> bool | None:
        info = CGSessionCopyCurrentDictionary()
        if not info:
            return None
        return bool(info.get("CGSSessionScreenIsLocked", 0))

    if locked() is None:
        raise RuntimeError("no session dictionary")
    return locked, "Quartz session dictionary"


def _macos_ioreg_probe():
    def locked() -> bool | None:
        out = _run(["/usr/sbin/ioreg", "-n", "Root", "-d1", "-a"])
        if out is None or "CGSSessionScreenIsLocked" not in out:
            return None
        # The plist prints the key immediately followed by <true/> or <false/>.
        tail = out.split("CGSSessionScreenIsLocked", 1)[1][:64]
        return "<true/>" in tail

    if locked() is None:
        raise RuntimeError("ioreg reports no lock state")
    return locked, "ioreg"


# -- Linux ---------------------------------------------------------------


def _linux_loginctl_probe():
    targets = [os.environ.get("XDG_SESSION_ID"), "self", "auto"]
    for target in targets:
        if not target:
            continue
        out = _run(["loginctl", "show-session", target, "-p", "LockedHint"])
        if out is None or "LockedHint=" not in out:
            continue

        def locked(target: str = target) -> bool | None:
            text = _run(["loginctl", "show-session", target, "-p", "LockedHint"])
            if text is None or "LockedHint=" not in text:
                return None
            return text.split("LockedHint=", 1)[1].strip().startswith("yes")

        return locked, "logind LockedHint"
    raise RuntimeError("loginctl reports no LockedHint")


def _linux_screensaver_probe():
    cmd = [
        "gdbus", "call", "--session",
        "--dest", "org.freedesktop.ScreenSaver",
        "--object-path", "/org/freedesktop/ScreenSaver",
        "--method", "org.freedesktop.ScreenSaver.GetActive",
    ]

    def locked() -> bool | None:
        out = _run(cmd)
        if out is None:
            return None
        return "true" in out.lower()

    if locked() is None:
        raise RuntimeError("no screensaver on the session bus")
    return locked, "org.freedesktop.ScreenSaver"


def _probes():
    if sys.platform == "win32":
        return [_windows_probe]
    if sys.platform == "darwin":
        return [_macos_quartz_probe, _macos_ioreg_probe]
    return [_linux_loginctl_probe, _linux_screensaver_probe]


def _resolve():
    """Pick the first lock probe that actually answers on this machine."""
    global _backend
    if _backend is not _UNKNOWN:
        return _backend
    _backend = None
    for probe in _probes():
        try:
            _backend = probe()
            break
        except Exception:
            continue
    return _backend


def lock_support() -> str:
    """Name of the mechanism used to detect a lock, or '' when there is none."""
    resolved = _resolve()
    return resolved[1] if resolved else ""


def screen_locked() -> bool | None:
    """True/False when known, None when this machine cannot tell us."""
    resolved = _resolve()
    if resolved is None:
        return None
    try:
        return resolved[0]()
    except Exception:
        return None


class SessionWatch:
    """Polls for a lock or a suspend on behalf of a running worker loop.

    Suspend is inferred from a gap between two clock readings.  Neither clock
    alone is enough across platforms: Linux freezes ``monotonic`` across a
    suspend while the wall clock keeps going, and Windows does the opposite.
    Comparing both against the caller's polling cadence catches either.
    """

    SUSPEND_GAP = 10.0  # s; far longer than any legitimate poll interval
    LOCK_POLL = 2.0  # s; the lock probe can cost a subprocess, so throttle it

    def __init__(self, watch_lock: bool = True, clock=time.monotonic, wall=time.time):
        self.watch_lock = watch_lock
        self._clock = clock
        self._wall = wall
        self._last_poll = 0.0
        self._last_wall = 0.0
        self._next_lock_check = 0.0

    def reset(self) -> None:
        now = self._clock()
        self._last_poll = now
        self._last_wall = self._wall()
        # Give a lock that is already up a moment to clear, so starting a run
        # from a just-unlocked screen does not stop instantly.
        self._next_lock_check = now + self.LOCK_POLL

    def poll(self) -> str | None:
        """Return LOCKED, SUSPENDED, or None. Call this from the worker loop."""
        now = self._clock()
        wall = self._wall()
        elapsed = now - self._last_poll
        wall_elapsed = wall - self._last_wall
        self._last_poll = now
        self._last_wall = wall

        if max(elapsed, wall_elapsed) >= self.SUSPEND_GAP:
            return SUSPENDED

        if not self.watch_lock or now < self._next_lock_check:
            return None

        self._next_lock_check = now + self.LOCK_POLL
        if screen_locked():
            return LOCKED
        return None
