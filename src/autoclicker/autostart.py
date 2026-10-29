"""Start the background instance when the user logs in.

Each platform has a per-user place for this that needs no admin rights:

- Windows: a value under ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
- macOS: a LaunchAgent plist in ``~/Library/LaunchAgents``
- Linux: an XDG autostart ``.desktop`` file in ``~/.config/autostart``

The entry runs ``--background --stay-online`` with the last session, so what
it does at login is whatever the Stay online tab was set to, schedule included.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from . import tray

ENTRY_NAME = "AdvancedAutoClicker"
LAUNCH_AGENT_LABEL = "com.jacksonsieben.autoclicker"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def command() -> list[str]:
    """What the login entry runs."""
    return tray._base_command() + ["--background", "--stay-online"]


def location() -> str:
    """Where the entry lives, for telling the user."""
    if sys.platform == "win32":
        return f"HKEY_CURRENT_USER\\{RUN_KEY}\\{ENTRY_NAME}"
    return str(_entry_path())


def _entry_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "autostart" / "advanced-auto-clicker.desktop"


def is_enabled() -> bool:
    if sys.platform == "win32":  # pragma: no cover - Windows only
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.QueryValueEx(key, ENTRY_NAME)
        except OSError:
            return False
        return True
    return _entry_path().exists()


def enable() -> str:
    """Create or refresh the login entry. Returns "" or an error message."""
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                winreg.SetValueEx(
                    key, ENTRY_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command())
                )
            return ""

        path = _entry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "darwin":
            path.write_bytes(
                plistlib.dumps(
                    {
                        "Label": LAUNCH_AGENT_LABEL,
                        "ProgramArguments": command(),
                        "RunAtLoad": True,
                    }
                )
            )
        else:
            path.write_text(_desktop_entry(), encoding="utf-8")
    except OSError as exc:
        return f"Could not set up start at login: {exc}"
    return ""


def disable() -> str:
    """Remove the login entry. Returns "" or an error message."""
    try:
        if sys.platform == "win32":  # pragma: no cover - Windows only
            import winreg

            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, ENTRY_NAME)
            except FileNotFoundError:
                pass
            return ""

        _entry_path().unlink(missing_ok=True)
    except OSError as exc:
        return f"Could not remove start at login: {exc}"
    return ""


def _desktop_entry() -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Advanced Auto Clicker\n"
        "Comment=Stay online in the background\n"
        f"Exec={' '.join(_desktop_quote(part) for part in command())}\n"
        "Terminal=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def _desktop_quote(arg: str) -> str:
    """Quote one Exec argument the way the Desktop Entry spec wants it."""
    if arg and not any(c in arg for c in ' \t"\'\\><~|&;$*?#()`'):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'
