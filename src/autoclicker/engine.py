"""The auto-clicking engine.

Deliberately free of UI imports: the TUI polls :class:`Engine` for state and
the engine never reaches back into the interface.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from enum import Enum

from . import backend, session
from .config import Config
from .schedule import Schedule


class State(str, Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    WAITING = "waiting"  # running, but held back by idle mode
    OFF_HOURS = "off_hours"  # keeper running, but outside its schedule


class StopReason(str, Enum):
    USER = "user"
    COUNT = "count"
    TIMER = "timer"
    ERROR = "error"
    LOCKED = "locked"
    SUSPENDED = "suspended"


_SESSION_REASONS = {
    session.LOCKED: StopReason.LOCKED,
    session.SUSPENDED: StopReason.SUSPENDED,
}


class ActivityMonitor:
    """Tracks real user input so idle mode can hold clicks back.

    Synthetic cursor moves made by the engine are filtered out, otherwise a
    fixed-position run would register its own clicks as user activity and
    pause itself forever.
    """

    MOVE_THRESHOLD = 5  # px; ignore jitter
    THROTTLE = 0.1  # s; cap how often we touch the timestamp

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mouse_listener = None
        self._key_listener = None
        self._last_activity = 0.0
        self._last_position: tuple[int, int] | None = None
        self._expected_move: tuple[int, int] | None = None
        self._muted_until = 0.0

    @property
    def last_activity(self) -> float:
        with self._lock:
            return self._last_activity

    def idle_for(self) -> float:
        return time.monotonic() - self.last_activity

    def expect_move(self, position: tuple[int, int]) -> None:
        """Tell the monitor the next move to ``position`` is ours, not the user's."""
        with self._lock:
            self._expected_move = position

    def mute(self, duration: float) -> None:
        """Ignore all input for ``duration`` seconds.

        The presence keeper synthesises key presses and mouse moves; without
        this it would read its own nudge as the user being active and never
        nudge again.
        """
        with self._lock:
            self._muted_until = time.monotonic() + duration

    def start(self) -> None:
        if not backend.AVAILABLE:
            return
        self.stop()
        with self._lock:
            self._last_activity = time.monotonic()
            self._last_position = None
            self._expected_move = None
            self._muted_until = 0.0

        try:
            self._mouse_listener = backend.mouse.Listener(on_move=self._on_move)
            self._mouse_listener.start()
        except Exception:
            self._mouse_listener = None

        try:
            self._key_listener = backend.keyboard.Listener(
                on_press=self._on_key, on_release=self._on_key
            )
            self._key_listener.start()
        except Exception:
            self._key_listener = None

    def stop(self) -> None:
        for listener in (self._mouse_listener, self._key_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass
        self._mouse_listener = None
        self._key_listener = None

    def _on_move(self, x, y) -> None:
        with self._lock:
            if time.monotonic() < self._muted_until:
                self._last_position = (x, y)
                return

            expected = self._expected_move
            if expected is not None and (
                abs(x - expected[0]) <= 2 and abs(y - expected[1]) <= 2
            ):
                self._expected_move = None
                self._last_position = (x, y)
                return

            now = time.monotonic()
            if now - self._last_activity <= self.THROTTLE:
                return
            previous = self._last_position
            if previous is None or (
                abs(x - previous[0]) > self.MOVE_THRESHOLD
                or abs(y - previous[1]) > self.MOVE_THRESHOLD
            ):
                self._last_position = (x, y)
                self._last_activity = now

    def _on_key(self, key) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._muted_until:
                return
            if now - self._last_activity > self.THROTTLE:
                self._last_activity = now


class Engine:
    """Runs the click loop on a worker thread."""

    def __init__(self, on_stop=None) -> None:
        self._on_stop = on_stop
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._monitor = ActivityMonitor()
        self._watch: session.SessionWatch | None = None
        self._controller = None

        self.state = State.STOPPED
        self.click_count = 0
        self.target_clicks = 0
        self.deadline: float | None = None
        self.last_error = ""

    # -- queries --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.state is not State.STOPPED

    @property
    def remaining(self) -> float | None:
        """Seconds left on the auto-stop timer, or None when it is off."""
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def cursor_position(self) -> tuple[int, int]:
        controller = self._ensure_controller()
        x, y = controller.position
        return int(x), int(y)

    # -- control --------------------------------------------------------

    def start(self, config: Config) -> list[str]:
        """Start clicking. Returns validation problems; empty means it started."""
        if self.running:
            return []

        problems = config.validate()
        if problems:
            return problems
        if not backend.AVAILABLE:
            return [f"Input backend unavailable: {backend.UNAVAILABLE_REASON}"]

        try:
            self._ensure_controller()
        except Exception as exc:
            return [f"Could not open the mouse device: {exc}"]

        self._stop_event.clear()
        self.click_count = 0
        self.last_error = ""
        self.target_clicks = config.repeat_times if config.repeat_mode == "times" else 0
        self.deadline = (
            time.monotonic() + config.timer_duration if config.timer_enabled else None
        )
        self.state = State.RUNNING

        # A suspend has to be caught whether or not the user asked to stop on
        # a lock: the machine coming back is not a session we should click in.
        self._watch = session.SessionWatch(watch_lock=config.stop_on_lock)
        self._watch.reset()

        if config.idle_enabled:
            self._monitor.start()

        self._thread = threading.Thread(
            target=self._run, args=(config,), name="click-worker", daemon=True
        )
        self._thread.start()
        return []

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()

    def toggle(self, config: Config) -> list[str]:
        if self.running:
            self.stop()
            return []
        return self.start(config)

    def shutdown(self) -> None:
        self._stop_event.set()
        self._monitor.stop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    # -- worker ---------------------------------------------------------

    def _ensure_controller(self):
        if self._controller is None:
            self._controller = backend.mouse.Controller()
        return self._controller

    def _run(self, config: Config) -> None:
        reason = StopReason.USER
        try:
            reason = self._loop(config)
        except Exception as exc:  # pragma: no cover - defensive
            self.last_error = str(exc)
            reason = StopReason.ERROR
        finally:
            self.state = State.STOPPED
            self.deadline = None
            self._monitor.stop()
            if self._on_stop is not None:
                try:
                    self._on_stop(reason, self.click_count, self.last_error)
                except Exception:
                    pass

    def _loop(self, config: Config) -> StopReason:
        controller = self._ensure_controller()
        button = backend.button_for(config.button)
        interval = config.interval
        fixed = config.location_mode == "fixed"
        target = (config.x, config.y) if fixed else None
        idle_enabled = config.idle_enabled
        idle_delay = config.idle_delay

        next_click = time.monotonic()

        watch = self._watch

        while not self._stop_event.is_set():
            now = time.monotonic()

            if self.deadline is not None and now >= self.deadline:
                return StopReason.TIMER
            if self.target_clicks and self.click_count >= self.target_clicks:
                return StopReason.COUNT

            if watch is not None:
                event = watch.poll()
                if event is not None:
                    return _SESSION_REASONS[event]

            # Idle mode: hold off while the user is actively working, and keep
            # the next click scheduled for the moment they go quiet.
            if idle_enabled and self._monitor.idle_for() < idle_delay:
                self.state = State.WAITING
                next_click = now
                if self._stop_event.wait(0.05):
                    break
                continue

            self.state = State.RUNNING

            if now < next_click:
                # Wake up often enough to stay responsive to stop/idle events.
                self._stop_event.wait(min(next_click - now, 0.05))
                continue

            if fixed:
                self._monitor.expect_move(target)
                controller.position = target
            controller.click(button)

            self.click_count += 1
            # Schedule from the previous slot so intervals do not drift, but
            # never try to catch up on a backlog after an idle pause.
            next_click = max(next_click + interval, now)

        return StopReason.USER


class Keeper:
    """Keeps Microsoft Teams (and anything else watching the OS idle timer)
    from flipping you to Away.

    Presence in Teams follows the operating system's idle timer, not the Teams
    window, so the only thing that has to happen is a real input event every
    few minutes.  This taps a key that nothing is bound to and/or nudges the
    pointer a pixel and back.

    It stops itself if the machine suspends, but not when the screen locks: a
    locked screen is exactly when people want it still running.

    With a schedule it runs all day and only nudges inside the schedule's
    windows. The auto-stop timer does not apply, and a suspend does not stop
    it either: tomorrow's 09:00 has to work after the laptop slept overnight.
    """

    NUDGE_MUTE = 0.5  # s; ignore our own synthetic input for this long

    def __init__(self, on_stop=None) -> None:
        self._on_stop = on_stop
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._monitor = ActivityMonitor()
        self._watch: session.SessionWatch | None = None
        self._mouse = None
        self._keyboard = None

        self.state = State.STOPPED
        self.nudge_count = 0
        self.skipped_count = 0
        self.deadline: float | None = None
        self.last_nudge: float | None = None
        self.last_error = ""
        self.schedule: Schedule | None = None

    # -- queries --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.state is not State.STOPPED

    @property
    def remaining(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    # -- control --------------------------------------------------------

    def start(self, config: Config) -> list[str]:
        """Start nudging. Returns validation problems; empty means it started."""
        if self.running:
            return []

        problems = config.validate_keeper()
        if problems:
            return problems
        if not backend.AVAILABLE:
            return [f"Input backend unavailable: {backend.UNAVAILABLE_REASON}"]

        try:
            self._ensure_controllers(config.keeper_method)
        except Exception as exc:
            return [f"Could not open the input device: {exc}"]

        self._stop_event.clear()
        self.nudge_count = 0
        self.skipped_count = 0
        self.last_nudge = None
        self.last_error = ""
        self.schedule = config.keeper_schedule if config.keeper_schedule_enabled else None
        self.deadline = (
            time.monotonic() + config.keeper_timer_duration
            if config.keeper_timer_enabled and self.schedule is None
            else None
        )
        self.state = State.RUNNING

        self._watch = session.SessionWatch(watch_lock=False)
        self._watch.reset()

        if config.keeper_only_when_idle:
            self._monitor.start()

        self._thread = threading.Thread(
            target=self._run, args=(config,), name="keeper-worker", daemon=True
        )
        self._thread.start()
        return []

    def stop(self) -> None:
        if not self.running:
            return
        self._stop_event.set()

    def toggle(self, config: Config) -> list[str]:
        if self.running:
            self.stop()
            return []
        return self.start(config)

    def shutdown(self) -> None:
        self._stop_event.set()
        self._monitor.stop()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    # -- worker ---------------------------------------------------------

    def _ensure_controllers(self, method: str):
        if method in ("key", "both") and self._keyboard is None:
            self._keyboard = backend.keyboard.Controller()
        if method in ("mouse", "both") and self._mouse is None:
            self._mouse = backend.mouse.Controller()

    def _run(self, config: Config) -> None:
        reason = StopReason.USER
        try:
            reason = self._loop(config)
        except Exception as exc:  # pragma: no cover - defensive
            self.last_error = str(exc)
            reason = StopReason.ERROR
        finally:
            self.state = State.STOPPED
            self.deadline = None
            self._monitor.stop()
            if self._on_stop is not None:
                try:
                    self._on_stop(reason, self.nudge_count, self.last_error)
                except Exception:
                    pass

    @staticmethod
    def _now() -> dt.datetime:
        """Local wall-clock time; a seam for the tests."""
        return dt.datetime.now()

    def _nudge(self, method: str, key) -> None:
        # Mute first: the listener must not read our own events as the user
        # returning, or "only when idle" would suppress every later nudge.
        self._monitor.mute(self.NUDGE_MUTE)

        if method in ("key", "both"):
            self._keyboard.press(key)
            self._keyboard.release(key)

        if method in ("mouse", "both"):
            x, y = self._mouse.position
            x, y = int(x), int(y)
            self._mouse.position = (x + 1, y)
            self._mouse.position = (x, y)

    def _loop(self, config: Config) -> StopReason:
        method = config.keeper_method
        key = backend.key_for(config.keeper_key) if method in ("key", "both") else None
        interval = config.keeper_interval
        only_when_idle = config.keeper_only_when_idle
        idle_delay = config.keeper_idle_delay
        watch = self._watch

        next_nudge = time.monotonic()

        while not self._stop_event.is_set():
            now = time.monotonic()

            if self.deadline is not None and now >= self.deadline:
                return StopReason.TIMER

            if watch is not None:
                event = watch.poll()
                if event is not None:
                    if self.schedule is None:
                        return _SESSION_REASONS[event]
                    watch.reset()

            if self.schedule is not None and not self.schedule.is_active(self._now()):
                self.state = State.OFF_HOURS
                # Nudge as soon as the next window opens.
                next_nudge = now
                self._stop_event.wait(1.0)
                continue

            if now < next_nudge:
                self._stop_event.wait(min(next_nudge - now, 0.25))
                continue

            # Someone at the keyboard has already reset the OS idle timer, so
            # there is nothing to do and no reason to interfere.
            if only_when_idle and self._monitor.idle_for() < idle_delay:
                self.state = State.WAITING
                self.skipped_count += 1
                next_nudge = now + interval
                continue

            self.state = State.RUNNING
            self._nudge(method, key)
            self.nudge_count += 1
            self.last_nudge = now
            next_nudge = now + interval

        return StopReason.USER
