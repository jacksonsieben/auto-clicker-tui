"""Run without a terminal: a background process with a system-tray icon.

The TUI is only alive for as long as its terminal window is, which makes the
clicker useless the moment you want to close the window and keep working.
This module runs the same :class:`~autoclicker.engine.Engine` and
:class:`~autoclicker.engine.Keeper` in a detached process that owns nothing but
a tray icon and the global hotkeys.

The icon needs ``pystray`` and ``Pillow``, which ship by default on Windows
only.  Without them the process still runs headless and the global hotkeys
still work; ``--stop-background`` is then the way to end it.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import backend, hotkeys
from .config import LAST_SESSION, Config, config_dir
from .engine import Engine, Keeper, State, StopReason

PID_FILE = "background.pid"
POLL_INTERVAL = 1.0

pystray = None
Image = None
ImageDraw = None
UNAVAILABLE_REASON = ""

try:  # pragma: no cover - depends on what is installed
    import pystray  # type: ignore  # noqa: F811
    from PIL import Image, ImageDraw  # type: ignore  # noqa: F811
except Exception as exc:  # pragma: no cover
    UNAVAILABLE_REASON = str(exc) or exc.__class__.__name__


def icon_available() -> bool:
    """True when a tray icon can be drawn; False means headless background."""
    return pystray is not None and Image is not None


# -- talking to a running background instance -----------------------------


def pid_file() -> Path:
    return config_dir() / PID_FILE


if os.name == "nt":  # pragma: no cover - exercised on Windows only

    def _process_alive(pid: int) -> bool:
        # os.kill(pid, 0) is not a liveness probe on Windows - it terminates
        # the process with exit code 0. Ask the kernel instead.
        import ctypes

        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x00000102

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)

else:

    def _process_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # someone else's process, but it exists
        return True


def background_pid() -> int | None:
    """PID of the running background instance, or None if there is not one."""
    try:
        pid = int(pid_file().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid() or not _process_alive(pid):
        return None
    return pid


def _claim_pid_file() -> None:
    try:
        pid_file().write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _release_pid_file() -> None:
    try:
        path = pid_file()
        if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except (OSError, ValueError):
        pass


def stop_background() -> str:
    """Stop a running background instance. Returns a message for the user."""
    pid = background_pid()
    if pid is None:
        return "No background instance is running."
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return f"Could not stop the background instance (PID {pid}): {exc}"

    # Give it a moment to put its icon away before saying it is gone.
    for _ in range(20):
        if background_pid() is None:
            break
        time.sleep(0.1)
    return f"Stopped the background instance (PID {pid})."


# -- launching ------------------------------------------------------------


def _base_command() -> list[str]:
    """How to invoke this app again, frozen binary or source checkout alike."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "autoclicker"]


def background_command(
    profile: str | None = None, click: bool = False, keeper: bool = False
) -> list[str]:
    command = _base_command() + ["--tray"]
    if profile and profile != LAST_SESSION:
        command += ["--profile", profile]
    if click:
        command.append("--start")
    if keeper:
        command.append("--stay-online")
    return command


def _spawn(command: list[str], detached: bool) -> str:
    """Start a sibling process. Returns "" or an error message."""
    kwargs: dict = {"close_fds": True}
    if detached:
        kwargs.update(
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        CREATE_NEW_CONSOLE = 0x00000010
        if detached:
            kwargs["creationflags"] = (
                DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
            )
        else:
            kwargs["creationflags"] = CREATE_NEW_CONSOLE
    elif detached:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(command, **kwargs)
    except OSError as exc:
        return str(exc)
    return ""


def launch_background(
    profile: str | None = None, click: bool = False, keeper: bool = False
) -> str:
    """Start the background process and return "" or an error message."""
    pid = background_pid()
    if pid is not None:
        return f"A background instance is already running (PID {pid})."
    return _spawn(background_command(profile, click, keeper), detached=True)


# The terminals worth trying, in the order a desktop is likely to have them.
# Windows needs none of this: a new console is a process-creation flag.
POSIX_TERMINALS = [
    ["x-terminal-emulator", "-e"],
    ["gnome-terminal", "--"],
    ["konsole", "-e"],
    ["xfce4-terminal", "-e"],
    ["alacritty", "-e"],
    ["kitty", "-e"],
    ["foot"],
    ["ghostty", "-e"],
    ["xterm", "-e"],
]


def launch_tui(profile: str | None = None) -> str:
    """Open the settings window in a terminal. Returns "" or an error."""
    command = _base_command()
    if profile and profile != LAST_SESSION:
        command += ["--profile", profile]

    if os.name == "nt":
        return _spawn(command, detached=False)

    if sys.platform == "darwin":
        # `open -a Terminal` cannot pass arguments through, so drive Terminal
        # with AppleScript instead.
        line = " ".join(shlex.quote(part) for part in command)
        script = line.replace("\\", "\\\\").replace('"', '\\"')
        error = _spawn(
            [
                "osascript",
                "-e",
                f'tell application "Terminal" to do script "{script}"',
                "-e",
                'tell application "Terminal" to activate',
            ],
            detached=True,
        )
        if not error:
            return ""

    for terminal in POSIX_TERMINALS:
        if shutil.which(terminal[0]) is None:
            continue
        return _spawn(terminal + command, detached=True)
    return "No terminal emulator found - run 'autoclicker' yourself to change settings."


# -- the icon -------------------------------------------------------------

ICON_COLORS = {
    "idle": (125, 133, 144),
    "clicking": (63, 185, 80),
    "waiting": (210, 153, 34),
    "keeper": (79, 140, 255),
}


def _icon_image(state: str):  # pragma: no cover - needs Pillow
    """A filled dot in the colour of the current state, with a ring around it."""
    size = 64
    colour = ICON_COLORS.get(state, ICON_COLORS["idle"])
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), outline=colour + (255,), width=5)
    draw.ellipse((19, 19, size - 19, size - 19), fill=colour + (255,))
    return image


class BackgroundRunner:
    """Owns the engine, the keeper and the hotkeys with no terminal attached."""

    def __init__(
        self,
        config: Config | None = None,
        profile: str = LAST_SESSION,
        click: bool = False,
        keeper: bool = False,
    ) -> None:
        self.config = config or Config.load(profile)
        self.profile = profile
        self.engine = Engine(on_stop=self._on_engine_stop)
        self.keeper = Keeper(on_stop=self._on_keeper_stop)
        self.hotkey_manager = hotkeys.HotkeyManager()

        self._autostart_click = click
        self._autostart_keeper = keeper
        self._stopping = threading.Event()
        self._icon = None

    # -- state ----------------------------------------------------------

    @property
    def icon_state(self) -> str:
        if self.engine.state is State.RUNNING:
            return "clicking"
        if self.engine.state is State.WAITING:
            return "waiting"
        if self.keeper.running:
            return "keeper"
        return "idle"

    def status_text(self) -> str:
        if self.engine.running:
            clicks = f"{self.engine.click_count:,} clicks"
            if self.engine.state is State.WAITING:
                return f"Waiting for you to go idle - {clicks}"
            return f"Clicking - {clicks}"
        if self.keeper.running:
            nudges = f"{self.keeper.nudge_count:,} nudges"
            if self.keeper.state is State.WAITING:
                return f"Staying online (you are active) - {nudges}"
            return f"Staying online - {nudges}"
        return "Idle"

    def tooltip(self) -> str:
        return f"Advanced Auto Clicker - {self.status_text()}"

    # -- control --------------------------------------------------------

    def start_clicking(self) -> None:
        if self.keeper.running:
            self.keeper.stop()
        problems = self.engine.start(self.config)
        if problems:
            self._notify("\n".join(problems), "Cannot start")
        self._refresh()

    def stop_clicking(self) -> None:
        self.engine.stop()
        self._refresh()

    def toggle_clicking(self) -> None:
        if self.engine.running:
            self.stop_clicking()
        else:
            self.start_clicking()

    def start_keeper(self) -> None:
        if self.engine.running:
            self.engine.stop()
        problems = self.keeper.start(self.config)
        if problems:
            self._notify("\n".join(problems), "Cannot start")
        self._refresh()

    def stop_keeper(self) -> None:
        self.keeper.stop()
        self._refresh()

    def toggle_keeper(self) -> None:
        if self.keeper.running:
            self.stop_keeper()
        else:
            self.start_keeper()

    def stop_all(self) -> None:
        self.engine.stop()
        self.keeper.stop()
        self._refresh()

    def open_settings(self) -> None:
        """Hand back to the TUI.

        Both processes would otherwise claim the same global hotkeys and could
        both be clicking, so this one steps aside as the window opens.
        """
        error = launch_tui(self.profile)
        if error:
            self._notify(error, "Cannot open settings")
            return
        self.quit()

    def quit(self) -> None:
        self._stopping.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass

    # -- running --------------------------------------------------------

    def run(self) -> int:
        if not backend.AVAILABLE:
            print(backend.diagnose(), file=sys.stderr)
            return 1

        _claim_pid_file()
        self._install_signal_handlers()
        self.hotkey_manager.register(
            {
                self.config.hotkey_toggle: self.toggle_clicking,
                self.config.hotkey_stop: self.stop_all,
                self.config.hotkey_keeper: self.toggle_keeper,
            }
        )
        try:
            if icon_available():
                self._run_with_icon()
            else:
                self._run_headless()
        finally:
            self.hotkey_manager.unregister()
            self.engine.shutdown()
            self.keeper.shutdown()
            _release_pid_file()
        return 0

    def _install_signal_handlers(self) -> None:
        def handler(signum, frame):  # pragma: no cover - signal driven
            self.quit()

        for name in ("SIGTERM", "SIGINT"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):  # pragma: no cover
                    pass

    def _autostart(self) -> None:
        if self._autostart_click:
            self.start_clicking()
        elif self._autostart_keeper:
            self.start_keeper()

    def _run_headless(self) -> None:
        print(
            "Running in the background. "
            "Stop it with: autoclicker --stop-background\n"
            f"(No tray icon here: {UNAVAILABLE_REASON or 'pystray is not installed'})",
            flush=True,
        )
        self._autostart()
        while not self._stopping.wait(POLL_INTERVAL):
            pass

    def _run_with_icon(self) -> None:  # pragma: no cover - needs a desktop
        menu = pystray.Menu(
            # pystray has no title item; a disabled one is the idiom.
            pystray.MenuItem(
                lambda item: self.status_text(), lambda icon, item: None, enabled=False
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: "Stop clicking" if self.engine.running else "Start clicking",
                lambda icon, item: self.toggle_clicking(),
                default=True,
            ),
            pystray.MenuItem(
                lambda item: "Stop staying online"
                if self.keeper.running
                else "Start staying online",
                lambda icon, item: self.toggle_keeper(),
            ),
            pystray.MenuItem("Stop everything", lambda icon, item: self.stop_all()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Open settings window", lambda icon, item: self.open_settings()
            ),
            pystray.MenuItem("Quit", lambda icon, item: self.quit()),
        )
        self._icon = pystray.Icon(
            "advanced-auto-clicker",
            icon=_icon_image("idle"),
            title=self.tooltip(),
            menu=menu,
        )
        self._icon.run(setup=self._on_icon_ready)

    def _on_icon_ready(self, icon) -> None:  # pragma: no cover - needs a desktop
        icon.visible = True
        self._autostart()
        threading.Thread(target=self._poll, name="tray-poll", daemon=True).start()

    def _poll(self) -> None:  # pragma: no cover - needs a desktop
        state = None
        while not self._stopping.wait(POLL_INTERVAL):
            icon = self._icon
            if icon is None:
                continue
            try:
                icon.title = self.tooltip()
                if self.icon_state != state:
                    state = self.icon_state
                    icon.icon = _icon_image(state)
                icon.update_menu()
            except Exception:
                pass

    # -- feedback -------------------------------------------------------

    def _refresh(self) -> None:
        icon = self._icon
        if icon is None:
            return
        try:
            icon.title = self.tooltip()
            icon.icon = _icon_image(self.icon_state)
            icon.update_menu()
        except Exception:  # pragma: no cover - backend dependent
            pass

    def _notify(self, message: str, title: str = "Advanced Auto Clicker") -> None:
        icon = self._icon
        if icon is None:
            print(f"{title}: {message}", file=sys.stderr, flush=True)
            return
        try:
            icon.notify(message, title)
        except Exception:  # pragma: no cover - not every backend has notifications
            pass

    def _on_engine_stop(self, reason: StopReason, clicks: int, error: str) -> None:
        message = {
            StopReason.TIMER: f"Timer elapsed after {clicks:,} clicks.",
            StopReason.COUNT: f"Finished {clicks:,} clicks.",
            StopReason.LOCKED: f"Screen locked, so clicking stopped after {clicks:,} clicks.",
            StopReason.SUSPENDED: f"Machine suspended, so clicking stopped after {clicks:,} clicks.",
            StopReason.ERROR: f"Clicking failed: {error}",
        }.get(reason)
        if message:
            self._notify(message, "Stopped")
        self._refresh()

    def _on_keeper_stop(self, reason: StopReason, nudges: int, error: str) -> None:
        message = {
            StopReason.TIMER: f"Stopped staying online after {nudges:,} nudges.",
            StopReason.SUSPENDED: "Machine suspended, so staying online stopped.",
            StopReason.ERROR: f"Staying online failed: {error}",
        }.get(reason)
        if message:
            self._notify(message, "Stopped")
        self._refresh()


def run(profile: str = LAST_SESSION, click: bool = False, keeper: bool = False) -> int:
    """Entry point for ``autoclicker --tray``."""
    existing = background_pid()
    if existing is not None:
        print(
            f"A background instance is already running (PID {existing}).",
            file=sys.stderr,
        )
        return 1
    return BackgroundRunner(
        Config.load(profile), profile=profile, click=click, keeper=keeper
    ).run()
