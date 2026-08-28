"""Tests for the parts that must behave identically on all three platforms."""

import threading
import time

import pytest

from autoclicker import backend, hotkeys, session
from autoclicker.config import Config
from autoclicker.engine import ActivityMonitor, Engine, Keeper, StopReason


# -- hotkey parsing -----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("f6", "f6"),
        ("F6", "f6"),
        ("  f6  ", "f6"),
        ("Ctrl + Shift+A", "ctrl+shift+a"),
        ("shift+ctrl+a", "ctrl+shift+a"),  # modifiers get a canonical order
        ("control+alt+delete", "ctrl+alt+delete"),
        ("cmd+q", "cmd+q"),
        ("win+q", "cmd+q"),
        ("escape", "esc"),
        ("<ctrl>+<f7>", "ctrl+f7"),
    ],
)
def test_normalize(raw, expected):
    assert hotkeys.normalize(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ctrl", "ctrl+shift", "f6+f7", "notakey"])
def test_normalize_rejects_bad_input(raw):
    with pytest.raises(hotkeys.HotkeyError):
        hotkeys.normalize(raw)


def test_to_pynput():
    assert hotkeys.to_pynput("ctrl+shift+a") == "<ctrl>+<shift>+a"
    assert hotkeys.to_pynput("f6") == "<f6>"


def test_display():
    assert hotkeys.display("ctrl+shift+a") == "Ctrl+Shift+A"
    assert hotkeys.display("f6") == "F6"


# -- config -------------------------------------------------------------


def test_interval_arithmetic():
    config = Config(interval_hours=1, interval_minutes=2, interval_seconds=3, interval_ms=500)
    assert config.interval == pytest.approx(3723.5)


def test_interval_never_zero():
    assert Config(interval_seconds=0).interval == 0.001


def test_timer_duration():
    assert Config(timer_hours=1, timer_minutes=1, timer_seconds=1).timer_duration == 3661


def test_validate_flags_zero_timer():
    problems = Config(timer_enabled=True, timer_seconds=0).validate()
    assert any("timer" in p.lower() for p in problems)


def test_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    original = Config(interval_ms=250, button="middle", repeat_mode="times", x=7, y=9)
    assert Config.from_dict(original.to_dict()) == original


def test_reads_legacy_tkinter_config():
    """Configs written by the old GUI must still load."""
    legacy = {
        "click_interval": {"hours": "0", "minutes": "1", "seconds": "2", "milliseconds": "3"},
        "mouse_button": "right",
        "click_repeat": {"mode": "times", "times": "42"},
        "click_location": {"mode": "pick", "x": "100", "y": "200"},
        "idle_mode_detection": {"enabled": True, "delay": "2.5"},
        "auto_stop_timer": {"enabled": True, "hours": "0", "minutes": "5", "seconds": "0"},
        "hotkeys": {"start_stop": "f8", "stop": "f9"},
        "version": "1.0",
    }
    config = Config.from_dict(legacy)
    assert config.interval_minutes == 1
    assert config.button == "right"
    assert config.repeat_times == 42
    assert config.location_mode == "fixed"  # the old app called this "pick"
    assert (config.x, config.y) == (100, 200)
    assert config.idle_delay == 2.5
    assert config.timer_minutes == 5
    assert config.hotkey_toggle == "f8"


def test_bad_values_fall_back_to_defaults():
    config = Config.from_dict({"config_version": 2, "interval_seconds": "not a number"})
    assert config.interval_seconds == 1


def test_load_missing_profile_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert Config.load("does-not-exist") == Config()


# -- engine -------------------------------------------------------------


class FakeController:
    """Stands in for pynput's mouse Controller."""

    def __init__(self):
        self.clicks = []
        self.position = (0, 0)
        self._lock = threading.Lock()

    def click(self, button):
        with self._lock:
            self.clicks.append((self.position, button))


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(backend, "AVAILABLE", True)
    monkeypatch.setattr(backend, "button_for", lambda name: name)
    controller = FakeController()
    stopped = threading.Event()
    result = {}

    def on_stop(reason, clicks, error):
        result.update(reason=reason, clicks=clicks, error=error)
        stopped.set()

    eng = Engine(on_stop=on_stop)
    monkeypatch.setattr(eng, "_ensure_controller", lambda: controller)
    eng.controller = controller
    eng.stopped = stopped
    eng.result = result
    yield eng
    eng.shutdown()


def test_stops_after_fixed_count(engine):
    config = Config(interval_seconds=0, interval_ms=1, repeat_mode="times", repeat_times=5)
    assert engine.start(config) == []
    assert engine.stopped.wait(5), "engine did not stop"
    assert engine.result["reason"] is StopReason.COUNT
    assert engine.click_count == 5
    assert len(engine.controller.clicks) == 5


def test_clicks_at_fixed_position(engine):
    config = Config(
        interval_seconds=0, interval_ms=1, repeat_mode="times", repeat_times=3,
        location_mode="fixed", x=321, y=123,
    )
    engine.start(config)
    assert engine.stopped.wait(5)
    assert all(pos == (321, 123) for pos, _ in engine.controller.clicks)


def test_uses_selected_button(engine):
    config = Config(
        interval_seconds=0, interval_ms=1, repeat_mode="times", repeat_times=2, button="right"
    )
    engine.start(config)
    assert engine.stopped.wait(5)
    assert all(button == "right" for _, button in engine.controller.clicks)


def test_auto_stop_timer_fires(engine):
    config = Config(interval_seconds=0, interval_ms=5, timer_enabled=True, timer_seconds=1)
    started = time.monotonic()
    engine.start(config)
    assert engine.stopped.wait(5)
    assert engine.result["reason"] is StopReason.TIMER
    assert 0.8 <= time.monotonic() - started <= 2.5


def test_user_stop_is_prompt(engine):
    engine.start(Config(interval_seconds=1))
    time.sleep(0.2)
    engine.stop()
    assert engine.stopped.wait(2), "stop should not wait out the full interval"
    assert engine.result["reason"] is StopReason.USER


def test_start_rejects_invalid_config(engine):
    problems = engine.start(Config(timer_enabled=True, timer_seconds=0))
    assert problems and not engine.running


def test_interval_is_honoured(engine):
    config = Config(
        interval_seconds=0, interval_ms=100, repeat_mode="times", repeat_times=4
    )
    started = time.monotonic()
    engine.start(config)
    assert engine.stopped.wait(5)
    # 4 clicks at 100ms: the first is immediate, so ~300ms of waiting.
    assert 0.25 <= time.monotonic() - started <= 0.8


# --- session watching ----------------------------------------------------


class FakeClock:
    """A monotonic-looking clock that only moves when told to."""

    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_session_watch_quiet_by_default():
    clock, wall = FakeClock(), FakeClock(50_000.0)
    watch = session.SessionWatch(watch_lock=False, clock=clock, wall=wall)
    watch.reset()
    for _ in range(5):
        clock.advance(0.1)
        wall.advance(0.1)
        assert watch.poll() is None


def test_session_watch_detects_frozen_monotonic_suspend():
    # Linux: the monotonic clock stalls across a suspend, the wall clock does not.
    clock, wall = FakeClock(), FakeClock(50_000.0)
    watch = session.SessionWatch(watch_lock=False, clock=clock, wall=wall)
    watch.reset()
    clock.advance(0.1)
    wall.advance(600.0)
    assert watch.poll() == session.SUSPENDED


def test_session_watch_detects_frozen_wall_clock_suspend():
    # Windows: the wall clock is what stalls.
    clock, wall = FakeClock(), FakeClock(50_000.0)
    watch = session.SessionWatch(watch_lock=False, clock=clock, wall=wall)
    watch.reset()
    clock.advance(600.0)
    wall.advance(0.1)
    assert watch.poll() == session.SUSPENDED


def test_session_watch_reset_clears_the_gap():
    clock, wall = FakeClock(), FakeClock(50_000.0)
    watch = session.SessionWatch(watch_lock=False, clock=clock, wall=wall)
    watch.reset()
    clock.advance(600.0)
    wall.advance(600.0)
    watch.reset()
    assert watch.poll() is None


def test_session_watch_reports_a_lock(monkeypatch):
    clock, wall = FakeClock(), FakeClock(50_000.0)
    monkeypatch.setattr(session, "screen_locked", lambda: True)
    watch = session.SessionWatch(watch_lock=True, clock=clock, wall=wall)
    watch.reset()
    # reset() holds the first lock probe off, so the poll has to wait it out.
    clock.advance(session.SessionWatch.LOCK_POLL + 0.1)
    wall.advance(session.SessionWatch.LOCK_POLL + 0.1)
    assert watch.poll() == session.LOCKED


def test_session_watch_ignores_a_lock_when_not_asked(monkeypatch):
    clock, wall = FakeClock(), FakeClock(50_000.0)
    monkeypatch.setattr(session, "screen_locked", lambda: True)
    watch = session.SessionWatch(watch_lock=False, clock=clock, wall=wall)
    watch.reset()
    clock.advance(5.0)
    wall.advance(5.0)
    assert watch.poll() is None


def test_lock_support_is_a_string():
    assert isinstance(session.lock_support(), str)


# --- keeper --------------------------------------------------------------


def test_keeper_keys_all_resolve():
    for name in backend.KEEPER_KEYS:
        assert backend.key_for(name) is not None


def test_key_for_falls_back_to_shift():
    assert backend.key_for("nonsense-key") is backend.key_for("shift")


def test_keeper_config_round_trip():
    config = Config(
        keeper_method="both",
        keeper_key="scroll_lock",
        keeper_interval_minutes=2,
        keeper_interval_seconds=30,
        keeper_timer_hours=4,
        stop_on_lock=False,
    )
    assert config.keeper_interval == 150.0
    assert config.keeper_timer_duration == 4 * 3600
    assert Config.from_dict(config.to_dict()) == config


def test_old_configs_load_without_the_new_fields():
    # A profile written by an earlier version has none of the keeper keys.
    legacy = Config().to_dict()
    for key in list(legacy):
        if key.startswith("keeper_") or key == "stop_on_lock":
            del legacy[key]
    restored = Config.from_dict(legacy)
    assert restored.stop_on_lock is True
    assert restored.keeper_key == "f15"


def test_keeper_interval_has_a_floor():
    assert Config(keeper_interval_minutes=0, keeper_interval_seconds=0).keeper_interval == 1.0


def test_validate_keeper_flags_a_timer_of_zero():
    config = Config(
        keeper_timer_enabled=True,
        keeper_timer_hours=0,
        keeper_timer_minutes=0,
        keeper_timer_seconds=0,
    )
    assert config.validate_keeper()


def test_validate_keeper_accepts_the_defaults():
    assert Config().validate_keeper() == []


def test_activity_monitor_mute_ignores_synthetic_input():
    monitor = ActivityMonitor()
    monitor._muted_until = 0.0
    monitor.mute(5.0)
    before = monitor.idle_for()
    monitor._on_key(None)
    monitor._on_move(10, 10)
    # Muted, so neither event counted as the user coming back.
    assert monitor.idle_for() >= before


class FakeKeeperController:
    """Stands in for both pynput controllers the keeper drives."""

    def __init__(self):
        self.presses = []
        self.position = (100, 100)

    def press(self, key):
        self.presses.append(key)

    def release(self, key):
        pass


def test_keeper_nudges_the_keyboard_and_the_mouse():
    keeper = Keeper()
    keyboard, mouse = FakeKeeperController(), FakeKeeperController()
    keeper._keyboard, keeper._mouse = keyboard, mouse
    keeper._nudge("both", "F15")
    assert keyboard.presses == ["F15"]
    # The pointer must end up exactly where it started.
    assert mouse.position == (100, 100)


def test_keeper_starts_out_stopped():
    keeper = Keeper()
    assert not keeper.running
    assert keeper.remaining is None
