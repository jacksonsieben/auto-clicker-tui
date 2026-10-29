"""Working hours for the presence keeper, and starting at login."""

import datetime as dt
import sys
import threading
import time

import pytest

from autoclicker import autostart, cli, tray
from autoclicker.config import Config
from autoclicker.engine import Keeper, State
from autoclicker.schedule import Schedule, ScheduleError, parse_days, parse_windows

# 2026-11-02 is a Monday.
MONDAY = dt.date(2026, 11, 2)


def at(day_offset: int, clock: str) -> dt.datetime:
    hours, minutes = map(int, clock.split(":"))
    return dt.datetime.combine(MONDAY + dt.timedelta(days=day_offset), dt.time(hours, minutes))


# -- parsing ------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("09:00-12:00, 13:00-18:00", [(540, 720), (780, 1080)]),
        ("9-12; 13-18", [(540, 720), (780, 1080)]),
        ("9h-12h30", [(540, 750)]),
        ("08:30 to 17:00", [(510, 1020)]),
        ("22:00-02:00", [(1320, 120)]),
        ("00:00-24:00", [(0, 1440)]),
    ],
)
def test_parse_windows(text, expected):
    assert parse_windows(text) == expected


@pytest.mark.parametrize("text", ["", "9", "09:00-", "25:00-26:00", "09:60-10:00", "10:00-10:00", "24:00-10:00"])
def test_parse_windows_rejects_bad_input(text):
    with pytest.raises(ScheduleError):
        parse_windows(text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mon-fri", [0, 1, 2, 3, 4]),
        ("Mon, Wed, Fri", [0, 2, 4]),
        ("monday-tuesday", [0, 1]),
        ("fri-mon", [0, 4, 5, 6]),
        ("daily", list(range(7))),
        ("", list(range(7))),
        ("weekends", [5, 6]),
    ],
)
def test_parse_days(text, expected):
    assert parse_days(text) == expected


@pytest.mark.parametrize("text", ["funday", "mon-tue-wed", ",,"])
def test_parse_days_rejects_bad_input(text):
    with pytest.raises(ScheduleError):
        parse_days(text)


# -- when it is active --------------------------------------------------

WORKDAY = Schedule.parse("09:00-12:00, 13:00-18:00", "mon-fri")


@pytest.mark.parametrize(
    "when,active",
    [
        (at(0, "08:59"), False),
        (at(0, "09:00"), True),
        (at(0, "11:59"), True),
        (at(0, "12:00"), False),  # the end is exclusive: lunch starts at 12:00
        (at(0, "12:30"), False),
        (at(0, "13:00"), True),
        (at(0, "17:59"), True),
        (at(0, "18:00"), False),
        (at(5, "10:00"), False),  # Saturday
    ],
)
def test_is_active(when, active):
    assert WORKDAY.is_active(when) is active


def test_next_start_is_after_lunch_on_the_same_day():
    assert WORKDAY.next_start(at(0, "12:15")) == at(0, "13:00")


def test_next_start_skips_the_weekend():
    assert WORKDAY.next_start(at(4, "18:30")) == at(7, "09:00")


def test_current_end():
    assert WORKDAY.current_end(at(0, "10:00")) == at(0, "12:00")
    assert WORKDAY.current_end(at(0, "12:30")) is None


def test_overnight_window_belongs_to_the_day_it_starts():
    night = Schedule.parse("22:00-02:00", "fri")
    assert night.is_active(at(4, "23:00"))
    assert night.is_active(at(5, "01:30"))  # Saturday morning, Friday's window
    assert not night.is_active(at(5, "23:00"))  # Saturday night is not Friday
    assert not night.is_active(at(3, "01:30"))


def test_describe():
    assert WORKDAY.describe() == "09:00-12:00, 13:00-18:00 on weekdays"
    assert Schedule.parse("22:00-24:00", "sat,sun").describe() == "22:00-00:00 on weekends"


# -- config -------------------------------------------------------------


def test_schedule_is_off_by_default_and_round_trips():
    config = Config()
    assert config.keeper_schedule_enabled is False
    config.keeper_schedule_enabled = True
    config.keeper_schedule_times = "08:00-17:00"
    assert Config.from_dict(config.to_dict()) == config


def test_validate_keeper_reports_a_bad_schedule():
    config = Config(keeper_schedule_enabled=True, keeper_schedule_times="nine to five")
    problems = config.validate_keeper()
    assert len(problems) == 1 and problems[0].startswith("Schedule:")


def test_a_bad_schedule_does_not_matter_while_it_is_off():
    assert Config(keeper_schedule_times="nonsense").validate_keeper() == []


def test_working_hours_override_a_zero_timer():
    config = Config(
        keeper_schedule_enabled=True,
        keeper_timer_enabled=True,
        keeper_timer_hours=0,
        keeper_timer_minutes=0,
        keeper_timer_seconds=0,
    )
    assert config.validate_keeper() == []


# -- the keeper on a schedule -------------------------------------------


class FakeMouse:
    def __init__(self):
        self.moves = 0
        self._position = (10, 10)

    @property
    def position(self):
        return self._position

    @position.setter
    def position(self, value):
        self.moves += 1
        self._position = value


def run_keeper(monkeypatch, now: dt.datetime, seconds: float = 0.3):
    monkeypatch.setattr(Keeper, "_now", staticmethod(lambda: now))
    config = Config(
        keeper_method="mouse",
        keeper_only_when_idle=False,
        keeper_interval_seconds=1,
        keeper_schedule_enabled=True,
    )
    keeper = Keeper()
    keeper._mouse = FakeMouse()
    keeper.schedule = config.keeper_schedule
    keeper.state = State.RUNNING
    thread = threading.Thread(target=keeper._loop, args=(config,), daemon=True)
    thread.start()
    time.sleep(seconds)
    seen = keeper.state
    keeper._stop_event.set()
    thread.join(timeout=2)
    return keeper, seen


def test_keeper_waits_outside_working_hours(monkeypatch):
    keeper, state = run_keeper(monkeypatch, at(0, "12:30"))
    assert state is State.OFF_HOURS
    assert keeper.nudge_count == 0


def test_keeper_nudges_inside_working_hours(monkeypatch):
    keeper, state = run_keeper(monkeypatch, at(0, "10:00"))
    assert state is State.RUNNING
    assert keeper.nudge_count == 1


def test_runner_says_when_it_resumes(monkeypatch):
    monkeypatch.setattr(tray, "datetime", type("Frozen", (), {"now": staticmethod(lambda: at(0, "12:30"))}))
    runner = tray.BackgroundRunner(Config())
    runner.keeper.state = State.OFF_HOURS
    runner.keeper.schedule = WORKDAY
    assert runner.status_text() == "Staying online - outside working hours, resumes 13:00"
    assert runner.icon_state == "waiting"


# -- start at login -----------------------------------------------------


def test_autostart_command_stays_online_in_the_background():
    assert autostart.command()[-2:] == ["--background", "--stay-online"]


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="XDG autostart")
def test_autostart_round_trip_on_linux(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert not autostart.is_enabled()

    assert cli.main(["--autostart", "on"]) == 0
    entry = (tmp_path / "autostart" / "advanced-auto-clicker.desktop").read_text()
    assert "--background --stay-online" in entry
    assert "Start at login is on" in capsys.readouterr().out

    assert cli.main(["--autostart", "off"]) == 0
    assert not autostart.is_enabled()
    assert "Start at login is off" in capsys.readouterr().out


def test_desktop_quoting():
    assert autostart._desktop_quote("/usr/bin/python3") == "/usr/bin/python3"
    assert autostart._desktop_quote("/opt/My Apps/clicker") == '"/opt/My Apps/clicker"'
    assert autostart._desktop_quote('a"b$c') == '"a\\"b\\$c"'
