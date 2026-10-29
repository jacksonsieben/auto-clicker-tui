"""Working hours for the presence keeper.

A schedule is a set of time windows ("09:00-12:00, 13:00-18:00") on a set of
days ("mon-fri"). Both are typed as plain text, so they fit in a config file
and in a single input field, and are parsed here.

A window whose end is before its start runs past midnight (22:00-02:00) and
belongs to the day it starts on.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_ALIASES = {
    "": list(range(7)),
    "all": list(range(7)),
    "daily": list(range(7)),
    "every day": list(range(7)),
    "weekdays": [0, 1, 2, 3, 4],
    "weekends": [5, 6],
}
MINUTES_PER_DAY = 24 * 60

_TIME = re.compile(r"^(\d{1,2})(?:[:h.](\d{2}))?h?$")


class ScheduleError(ValueError):
    """The text does not describe a schedule. The message says why."""


def _parse_time(text: str, *, end: bool) -> int:
    """Minutes since midnight. "24:00" is allowed as an end time."""
    match = _TIME.match(text.strip().lower())
    if not match:
        raise ScheduleError(f"'{text.strip()}' is not a time - use HH:MM, like 09:00.")
    hours, minutes = int(match.group(1)), int(match.group(2) or 0)
    if minutes > 59 or hours > 24 or (hours == 24 and (minutes or not end)):
        raise ScheduleError(f"'{text.strip()}' is not a time of day.")
    return hours * 60 + minutes


def parse_windows(text: str) -> list[tuple[int, int]]:
    """Parse "09:00-12:00, 13:00-18:00" into (start, end) minute pairs."""
    windows = []
    for part in re.split(r"[,;]", text):
        if not part.strip():
            continue
        bounds = re.split(r"\s*(?:-|–|to)\s*", part.strip())
        if len(bounds) != 2:
            raise ScheduleError(f"'{part.strip()}' is not a range - use 09:00-12:00.")
        start = _parse_time(bounds[0], end=False)
        stop = _parse_time(bounds[1], end=True)
        if start == stop:
            raise ScheduleError(f"'{part.strip()}' starts and ends at the same time.")
        windows.append((start, stop))
    if not windows:
        raise ScheduleError("Add at least one time range, like 09:00-18:00.")
    return windows


def parse_days(text: str) -> list[int]:
    """Parse "mon-fri", "mon,wed,fri" or "daily" into weekday numbers (Mon=0)."""
    cleaned = text.strip().lower()
    if cleaned in DAY_ALIASES:
        return DAY_ALIASES[cleaned]

    days: set[int] = set()
    for part in re.split(r"[,;\s]+", cleaned):
        if not part:
            continue
        bounds = part.split("-")
        if len(bounds) > 2:
            raise ScheduleError(f"'{part}' is not a day or a range of days.")
        try:
            numbers = [DAY_NAMES.index(b[:3]) for b in bounds]
        except ValueError:
            raise ScheduleError(
                f"'{part}' is not a day - use mon, tue, wed, thu, fri, sat, sun."
            ) from None
        first, last = numbers[0], numbers[-1]
        # A range may wrap around the week: fri-mon is fri, sat, sun, mon.
        day = first
        days.add(day)
        while day != last:
            day = (day + 1) % 7
            days.add(day)
    if not days:
        raise ScheduleError("Pick at least one day.")
    return sorted(days)


def _clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class Schedule:
    windows: tuple[tuple[int, int], ...]
    days: tuple[int, ...]

    @classmethod
    def parse(cls, windows: str, days: str) -> "Schedule":
        return cls(tuple(parse_windows(windows)), tuple(parse_days(days)))

    def _spans(self, date: dt.date) -> list[tuple[dt.datetime, dt.datetime]]:
        """The windows that start on ``date``, as datetimes."""
        if date.weekday() not in self.days:
            return []
        midnight = dt.datetime.combine(date, dt.time())
        spans = []
        for start, stop in self.windows:
            if stop <= start:
                stop += MINUTES_PER_DAY
            spans.append(
                (midnight + dt.timedelta(minutes=start), midnight + dt.timedelta(minutes=stop))
            )
        return spans

    def is_active(self, when: dt.datetime) -> bool:
        # Yesterday's overnight windows can still be open this morning.
        for offset in (0, -1):
            for start, stop in self._spans(when.date() + dt.timedelta(days=offset)):
                if start <= when < stop:
                    return True
        return False

    def next_start(self, when: dt.datetime) -> dt.datetime | None:
        """When the next window opens after ``when``; None if none ever does."""
        for offset in range(8):
            starts = [
                start
                for start, _ in self._spans(when.date() + dt.timedelta(days=offset))
                if start > when
            ]
            if starts:
                return min(starts)
        return None

    def current_end(self, when: dt.datetime) -> dt.datetime | None:
        """When the window open at ``when`` closes; None if none is open."""
        ends = [
            stop
            for offset in (0, -1)
            for start, stop in self._spans(when.date() + dt.timedelta(days=offset))
            if start <= when < stop
        ]
        return max(ends) if ends else None

    def describe(self) -> str:
        times = ", ".join(f"{_clock(a)}-{_clock(b % MINUTES_PER_DAY)}" for a, b in self.windows)
        return f"{times} on {describe_days(self.days)}"


def describe_days(days) -> str:
    days = sorted(days)
    if len(days) == 7:
        return "every day"
    if days == [0, 1, 2, 3, 4]:
        return "weekdays"
    if days == [5, 6]:
        return "weekends"
    return ", ".join(DAY_NAMES[d].title() for d in days)


def format_moment(moment: dt.datetime, now: dt.datetime) -> str:
    """"13:00" today, "Mon 09:00" any other day."""
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    return f"{DAY_NAMES[moment.weekday()].title()} {moment.strftime('%H:%M')}"
