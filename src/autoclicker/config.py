"""Configuration model and on-disk persistence.

Uses only the standard library so the install stays at two dependencies.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from .schedule import Schedule, ScheduleError

APP_NAME = "advanced-auto-clicker"
LAST_SESSION = "last_session"
CONFIG_VERSION = 3


def config_dir() -> Path:
    """Per-user config directory, following each platform's convention."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"

    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def profile_path(name: str) -> Path:
    return config_dir() / f"{safe_name(name)}.json"


def safe_name(name: str) -> str:
    """Reduce a user-supplied profile name to something safe on every OS."""
    cleaned = re.sub(r"[^\w\-. ]+", "_", name).strip(" .")
    return cleaned[:60] or "profile"


def list_profiles() -> list[str]:
    """Saved profile names, excluding the implicit last-session file."""
    names = sorted(p.stem for p in config_dir().glob("*.json"))
    return [n for n in names if n != LAST_SESSION]


@dataclass
class Config:
    # Click interval
    interval_hours: int = 0
    interval_minutes: int = 0
    interval_seconds: int = 1
    interval_ms: int = 0

    # What and where
    button: str = "left"  # left | right | middle
    repeat_mode: str = "forever"  # forever | times
    repeat_times: int = 100
    location_mode: str = "cursor"  # cursor | fixed
    x: int = 0
    y: int = 0

    # Idle mode
    idle_enabled: bool = False
    idle_delay: float = 1.0

    # Auto-stop timer
    timer_enabled: bool = False
    timer_hours: int = 0
    timer_minutes: int = 0
    timer_seconds: int = 30

    # Stop the clicker when the session goes away
    stop_on_lock: bool = True

    # Presence keeper (Microsoft Teams and friends)
    keeper_method: str = "key"  # key | mouse | both
    keeper_key: str = "f15"  # f15 | scroll_lock | shift
    keeper_interval_minutes: int = 1
    keeper_interval_seconds: int = 0
    keeper_only_when_idle: bool = True
    keeper_idle_delay: float = 30.0
    keeper_timer_enabled: bool = True
    keeper_timer_hours: int = 8
    keeper_timer_minutes: int = 0
    keeper_timer_seconds: int = 0
    # Working hours: only nudge inside these windows, on these days
    keeper_schedule_enabled: bool = False
    keeper_schedule_times: str = "09:00-12:00, 13:00-18:00"
    keeper_schedule_days: str = "mon-fri"

    # Global hotkeys
    hotkey_toggle: str = "f6"
    hotkey_stop: str = "f7"
    hotkey_keeper: str = "f8"

    @property
    def interval(self) -> float:
        """Click interval in seconds, floored at 1ms."""
        total = (
            self.interval_hours * 3600
            + self.interval_minutes * 60
            + self.interval_seconds
            + self.interval_ms / 1000
        )
        return max(0.001, total)

    @property
    def timer_duration(self) -> int:
        """Auto-stop duration in seconds (0 when it would be a no-op)."""
        return self.timer_hours * 3600 + self.timer_minutes * 60 + self.timer_seconds

    @property
    def keeper_interval(self) -> float:
        """Seconds between presence nudges, floored at 1s."""
        total = self.keeper_interval_minutes * 60 + self.keeper_interval_seconds
        return max(1.0, float(total))

    @property
    def keeper_timer_duration(self) -> int:
        """Keeper auto-stop duration in seconds (0 when it would be a no-op)."""
        return (
            self.keeper_timer_hours * 3600
            + self.keeper_timer_minutes * 60
            + self.keeper_timer_seconds
        )

    @property
    def keeper_schedule(self) -> Schedule:
        """The parsed working hours. Raises ScheduleError on bad text."""
        return Schedule.parse(self.keeper_schedule_times, self.keeper_schedule_days)

    def validate(self) -> list[str]:
        """Return human-readable problems that block a run."""
        problems = []
        if self.interval <= 0:
            problems.append("Click interval must be greater than zero.")
        if self.repeat_mode == "times" and self.repeat_times < 1:
            problems.append("Click count must be at least 1.")
        if self.timer_enabled and self.timer_duration <= 0:
            problems.append("Auto-stop timer is on but the duration is zero.")
        if self.idle_enabled and self.idle_delay < 0:
            problems.append("Idle resume delay cannot be negative.")
        return problems

    def validate_keeper(self) -> list[str]:
        """Problems that block a presence-keeper run."""
        problems = []
        if self.keeper_interval_minutes * 60 + self.keeper_interval_seconds < 1:
            problems.append("Nudge interval must be at least 1 second.")
        if self.keeper_schedule_enabled:
            try:
                self.keeper_schedule
            except ScheduleError as exc:
                problems.append(f"Schedule: {exc}")
        elif self.keeper_timer_enabled and self.keeper_timer_duration <= 0:
            problems.append("Auto-stop timer is on but the duration is zero.")
        if self.keeper_only_when_idle and self.keeper_idle_delay < 0:
            problems.append("Idle threshold cannot be negative.")
        return problems

    # -- persistence ----------------------------------------------------

    def to_dict(self) -> dict:
        data = asdict(self)
        data["config_version"] = CONFIG_VERSION
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        if "config_version" not in data and "click_interval" in data:
            return cls._from_legacy(data)

        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for key, value in data.items():
            field = known.get(key)
            if field is None:
                continue
            try:
                kwargs[key] = _coerce(field.type, value)
            except (TypeError, ValueError):
                continue  # fall back to the dataclass default
        return cls(**kwargs)

    @classmethod
    def _from_legacy(cls, data: dict) -> "Config":
        """Read a config written by the Tkinter version of this app."""
        interval = data.get("click_interval", {})
        repeat = data.get("click_repeat", {})
        location = data.get("click_location", {})
        idle = data.get("idle_mode_detection", {})
        timer = data.get("auto_stop_timer", {})
        hotkeys = data.get("hotkeys", {})

        return cls(
            interval_hours=_int(interval.get("hours"), 0),
            interval_minutes=_int(interval.get("minutes"), 0),
            interval_seconds=_int(interval.get("seconds"), 1),
            interval_ms=_int(interval.get("milliseconds"), 0),
            button=data.get("mouse_button", "left"),
            repeat_mode=repeat.get("mode", "forever"),
            repeat_times=_int(repeat.get("times"), 100),
            # the old app called fixed coordinates "pick"
            location_mode="fixed" if location.get("mode") == "pick" else "cursor",
            x=_int(location.get("x"), 0),
            y=_int(location.get("y"), 0),
            idle_enabled=bool(idle.get("enabled", False)),
            idle_delay=_float(idle.get("delay"), 1.0),
            timer_enabled=bool(timer.get("enabled", False)),
            timer_hours=_int(timer.get("hours"), 0),
            timer_minutes=_int(timer.get("minutes"), 0),
            timer_seconds=_int(timer.get("seconds"), 30),
            hotkey_toggle=hotkeys.get("start_stop", "f6"),
            hotkey_stop=hotkeys.get("stop", "f7"),
        )

    def save(self, name: str = LAST_SESSION) -> Path:
        path = profile_path(name)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, name: str = LAST_SESSION) -> "Config":
        """Load a profile, falling back to defaults if it is missing or broken."""
        path = profile_path(name)
        if not path.exists():
            return cls()
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()


def _coerce(type_hint, value):
    """Cast JSON data onto a dataclass field type.

    Raises on junk so that :meth:`Config.from_dict` can drop the key and let
    the dataclass default stand, rather than silently substituting zero.
    """
    name = type_hint if isinstance(type_hint, str) else getattr(type_hint, "__name__", "")
    if name == "int":
        return int(str(value).strip())
    if name == "float":
        return float(str(value).strip())
    if name == "bool":
        return bool(value)
    return str(value)


def _int(value, default: int) -> int:
    try:
        return int(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


def _float(value, default: float) -> float:
    try:
        return float(str(value).strip() or default)
    except (TypeError, ValueError):
        return default
