"""The terminal interface."""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    OptionList,
    RadioButton,
    RadioSet,
    Rule,
    Static,
    Switch,
    TabbedContent,
    TabPane,
)

from . import backend, hotkeys, session
from .config import LAST_SESSION, Config, config_dir, list_profiles, profile_path, safe_name
from .engine import Engine, Keeper, State, StopReason
from .picker import LocationPicker
from .version import __version__

POLL_INTERVAL = 0.1


def _int_value(widget: Input, default: int = 0) -> int:
    try:
        return int((widget.value or "").strip() or default)
    except ValueError:
        return default


def _float_value(widget: Input, default: float = 0.0) -> float:
    try:
        return float((widget.value or "").strip() or default)
    except ValueError:
        return default


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


class ConfirmScreen(ModalScreen[bool]):
    """A yes/no modal."""

    BINDINGS = [("escape", "dismiss_false", "Cancel")]

    def __init__(self, message: str, confirm_label: str = "Confirm") -> None:
        super().__init__()
        self._message = message
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-body"):
            yield Label(self._message, id="modal-message")
            with Horizontal(id="modal-buttons"):
                yield Button(self._confirm_label, variant="error", id="modal-confirm")
                yield Button("Cancel", id="modal-cancel")

    @on(Button.Pressed, "#modal-confirm")
    def _confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#modal-cancel")
    def _cancel(self) -> None:
        self.dismiss(False)

    def action_dismiss_false(self) -> None:
        self.dismiss(False)


class CaptureScreen(ModalScreen[str | None]):
    """Blocking overlay shown while capturing a hotkey or a screen position.

    Capture happens through global OS listeners, not through Textual, so the
    terminal does not need focus and F-keys are not swallowed by the terminal.
    """

    def __init__(self, title: str, hint: str) -> None:
        super().__init__()
        self._title = title
        self._hint = hint
        self._capturer = None

    def compose(self) -> ComposeResult:
        with Vertical(id="modal-body"):
            yield Label(self._title, id="modal-message")
            yield Label(self._hint, id="modal-hint")
            yield Button("Cancel", id="modal-cancel")

    def on_mount(self) -> None:
        self._capturer = self._make_capturer()
        error = self._capturer.start()
        if error:
            self.dismiss(None)
            self.app.notify(error, severity="error", title="Capture failed")

    def _make_capturer(self):
        raise NotImplementedError

    def _finish(self, value) -> None:
        self.app.call_from_thread(self.dismiss, value)

    def _cancelled(self) -> None:
        self.app.call_from_thread(self.dismiss, None)

    @on(Button.Pressed, "#modal-cancel")
    def _cancel(self) -> None:
        if self._capturer is not None:
            self._capturer.cancel()
        self.dismiss(None)


class HotkeyCaptureScreen(CaptureScreen):
    def __init__(self) -> None:
        super().__init__(
            "Press the key combination to bind",
            "Modifiers are allowed (Ctrl, Alt, Shift, Cmd). Press Esc to cancel.",
        )

    def _make_capturer(self):
        return hotkeys.HotkeyRecorder(
            on_captured=lambda combo: self._finish(combo),
            on_cancelled=self._cancelled,
        )


class LocationCaptureScreen(CaptureScreen):
    def __init__(self) -> None:
        super().__init__(
            "Click anywhere on the screen",
            "The next click you make sets the target position. Press Esc to cancel.",
        )

    def _make_capturer(self):
        return LocationPicker(
            on_picked=lambda x, y: self._finish((x, y)),
            on_cancelled=self._cancelled,
        )


class AutoClickerApp(App):
    """Advanced Auto Clicker."""

    TITLE = "Advanced Auto Clicker"
    CSS_PATH = "tui.tcss"

    BINDINGS = [
        ("ctrl+r", "toggle", "Start/Stop"),
        ("ctrl+t", "keeper_toggle", "Stay online"),
        ("ctrl+s", "save_session", "Save"),
        ("f1", "show_help", "Help"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, config: Config | None = None) -> None:
        super().__init__()
        self.config_model = config or Config.load(LAST_SESSION)
        self.engine = Engine(on_stop=self._on_engine_stop)
        self.keeper = Keeper(on_stop=self._on_keeper_stop)
        self.hotkey_manager = hotkeys.HotkeyManager()
        self._suspended_hotkeys = False
        self._main_screen = None
        self._status_line = None

    # -- layout ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(id="tabs"):
            with TabPane("Clicking", id="tab-clicking"):
                yield from self._compose_clicking()
            with TabPane("Advanced", id="tab-advanced"):
                yield from self._compose_advanced()
            with TabPane("Stay online", id="tab-keeper"):
                yield from self._compose_keeper()
            with TabPane("Profiles", id="tab-profiles"):
                yield from self._compose_profiles()
            with TabPane("Help", id="tab-help"):
                yield from self._compose_help()

        with Vertical(id="statusbar"):
            yield Static("", id="status-line")
            with Horizontal(id="controls"):
                yield Button("Start", variant="success", id="btn-start")
                yield Button("Stop", variant="error", id="btn-stop", disabled=True)

        yield Footer()

    def _compose_clicking(self) -> ComposeResult:
        with VerticalScroll(classes="pane"):
            yield Label("Click interval", classes="section")
            with Horizontal(classes="field-row"):
                yield from self._number_field("Hours", "interval-hours")
                yield from self._number_field("Minutes", "interval-minutes")
                yield from self._number_field("Seconds", "interval-seconds")
                yield from self._number_field("Millisec", "interval-ms")
            yield Static("", id="interval-summary", classes="hint")

            yield Rule()
            yield Label("Mouse button", classes="section")
            with RadioSet(id="button-set"):
                yield RadioButton("Left", id="button-left")
                yield RadioButton("Right", id="button-right")
                yield RadioButton("Middle", id="button-middle")

            yield Rule()
            yield Label("Repeat", classes="section")
            with RadioSet(id="repeat-set"):
                yield RadioButton("Forever", id="repeat-forever")
                yield RadioButton("A fixed number of clicks", id="repeat-times")
            with Horizontal(classes="field-row"):
                yield from self._number_field("Clicks", "repeat-count")

            yield Rule()
            yield Label("Click location", classes="section")
            with RadioSet(id="location-set"):
                yield RadioButton("Wherever the cursor is", id="location-cursor")
                yield RadioButton("A fixed screen position", id="location-fixed")
            with Horizontal(classes="field-row"):
                yield from self._number_field("X", "pos-x")
                yield from self._number_field("Y", "pos-y")
                with Vertical(classes="field"):
                    yield Label(" ")
                    yield Button("Pick on screen", id="btn-pick")
            yield Static("", id="location-hint", classes="hint")

    def _compose_advanced(self) -> ComposeResult:
        with VerticalScroll(classes="pane"):
            yield Label("Idle mode", classes="section")
            with Horizontal(classes="toggle-row"):
                yield Switch(id="idle-switch")
                yield Label("Only click while you are not using the machine", classes="toggle-label")
            with Horizontal(classes="field-row"):
                yield from self._number_field("Resume after (s)", "idle-delay")
            yield Static(
                "Clicks pause the moment you move the mouse or type, and resume "
                "once you have been still for the delay above.",
                classes="hint",
            )
            yield Static("", id="idle-status", classes="hint")

            yield Rule()
            yield Label("Auto-stop timer", classes="section")
            with Horizontal(classes="toggle-row"):
                yield Switch(id="timer-switch")
                yield Label("Stop automatically after a set duration", classes="toggle-label")
            with Horizontal(classes="field-row"):
                yield from self._number_field("Hours", "timer-hours")
                yield from self._number_field("Minutes", "timer-minutes")
                yield from self._number_field("Seconds", "timer-seconds")
            yield Static("", id="timer-status", classes="hint")

            yield Rule()
            yield Label("When the session goes away", classes="section")
            with Horizontal(classes="toggle-row"):
                yield Switch(id="lock-switch")
                yield Label(
                    "Stop clicking if the screen locks or the PC suspends",
                    classes="toggle-label",
                )
            yield Static("", id="lock-status", classes="hint")

            yield Rule()
            yield Label("Global hotkeys", classes="section")
            yield Static(
                "These work while another window has focus, which is the point.",
                classes="hint",
            )
            with Horizontal(classes="field-row"):
                with Vertical(classes="field-wide"):
                    yield Label("Start / stop")
                    yield Input(id="hotkey-toggle", classes="hotkey-input")
                with Vertical(classes="field"):
                    yield Label(" ")
                    yield Button("Record", id="btn-record-toggle")
            with Horizontal(classes="field-row"):
                with Vertical(classes="field-wide"):
                    yield Label("Stop only")
                    yield Input(id="hotkey-stop", classes="hotkey-input")
                with Vertical(classes="field"):
                    yield Label(" ")
                    yield Button("Record", id="btn-record-stop")
            yield Static("", id="hotkey-status", classes="hint")

    def _compose_keeper(self) -> ComposeResult:
        with VerticalScroll(classes="pane"):
            yield Static(
                "Microsoft Teams takes your presence from the operating system's "
                "idle timer, not from its own window: a few minutes with no input "
                "and it flips you to Away. This sends one harmless input event "
                "often enough that the timer never gets there.",
                classes="hint",
            )

            yield Label("How to nudge", classes="section")
            with RadioSet(id="keeper-method-set"):
                yield RadioButton("A keystroke", id="keeper-method-key")
                yield RadioButton("A mouse nudge (1px and straight back)", id="keeper-method-mouse")
                yield RadioButton("Both", id="keeper-method-both")
            with RadioSet(id="keeper-key-set"):
                yield RadioButton("F15 - no keyboard has it, so nothing is bound to it", id="keeper-key-f15")
                yield RadioButton("Scroll Lock", id="keeper-key-scroll_lock")
                yield RadioButton("Shift", id="keeper-key-shift")

            yield Rule()
            yield Label("How often", classes="section")
            with Horizontal(classes="field-row"):
                yield from self._number_field("Minutes", "keeper-interval-minutes")
                yield from self._number_field("Seconds", "keeper-interval-seconds")
            yield Static("", id="keeper-interval-summary", classes="hint")

            yield Rule()
            yield Label("Stay out of the way", classes="section")
            with Horizontal(classes="toggle-row"):
                yield Switch(id="keeper-idle-switch")
                yield Label(
                    "Only nudge when the machine is already idle",
                    classes="toggle-label",
                )
            with Horizontal(classes="field-row"):
                yield from self._number_field("Idle for (s)", "keeper-idle-delay")
            yield Static(
                "With this on, nothing is sent while you are actually typing or "
                "moving the mouse - you have already reset the idle timer yourself.",
                classes="hint",
            )

            yield Rule()
            yield Label("Auto-stop timer", classes="section")
            with Horizontal(classes="toggle-row"):
                yield Switch(id="keeper-timer-switch")
                yield Label(
                    "Stop on its own after a set duration",
                    classes="toggle-label",
                )
            with Horizontal(classes="field-row"):
                yield from self._number_field("Hours", "keeper-timer-hours")
                yield from self._number_field("Minutes", "keeper-timer-minutes")
                yield from self._number_field("Seconds", "keeper-timer-seconds")
            yield Static("", id="keeper-timer-status", classes="hint")

            yield Rule()
            yield Label("Global hotkey", classes="section")
            with Horizontal(classes="field-row"):
                with Vertical(classes="field-wide"):
                    yield Label("Start / stop staying online")
                    yield Input(id="hotkey-keeper", classes="hotkey-input")
                with Vertical(classes="field"):
                    yield Label(" ")
                    yield Button("Record", id="btn-record-keeper")

            yield Rule()
            with Horizontal(classes="field-row"):
                yield Button("Start staying online", variant="success", id="btn-keeper-start")
                yield Button("Stop", variant="error", id="btn-keeper-stop", disabled=True)
            yield Static("", id="keeper-status", classes="hint")

    def _compose_profiles(self) -> ComposeResult:
        with VerticalScroll(classes="pane"):
            yield Label("Saved profiles", classes="section")
            yield Static(
                "Your current settings are always restored on the next launch. "
                "Named profiles let you keep several setups side by side.",
                classes="hint",
            )
            yield OptionList(id="profile-list")
            with Horizontal(classes="field-row"):
                with Vertical(classes="field-wide"):
                    yield Label("Profile name")
                    yield Input(placeholder="e.g. slow-grind", id="profile-name")
                with Vertical(classes="field"):
                    yield Label(" ")
                    yield Button("Save", variant="primary", id="btn-profile-save")
            with Horizontal(classes="field-row"):
                yield Button("Load selected", id="btn-profile-load")
                yield Button("Delete selected", variant="error", id="btn-profile-delete")
            yield Static(f"Stored in {config_dir()}", classes="hint")

    def _compose_help(self) -> ComposeResult:
        with VerticalScroll(classes="pane"):
            yield Static(HELP_TEXT, id="help-text")

    def _number_field(self, label: str, field_id: str) -> ComposeResult:
        with Vertical(classes="field"):
            yield Label(label)
            yield Input(id=field_id, type="number", classes="number")

    # -- lifecycle ------------------------------------------------------

    def _q(self, selector: str, kind):
        """Query the main screen, not whatever modal happens to be on top."""
        screen = self._main_screen if self._main_screen is not None else self.screen
        return screen.query_one(selector, kind)

    def on_mount(self) -> None:
        self._main_screen = self.screen
        self._status_line = self._q("#status-line", Static)
        self._apply_config(self.config_model)
        self.refresh_profiles()
        self._register_hotkeys()
        self.set_interval(POLL_INTERVAL, self._tick)

        problem = backend.diagnose()
        if problem:
            self.notify(problem, severity="warning", title="Heads up", timeout=15)

    def on_unmount(self) -> None:
        self.engine.shutdown()
        self.keeper.shutdown()
        self.hotkey_manager.unregister()

    def action_quit(self) -> None:
        self._collect_config().save(LAST_SESSION)
        self.exit()

    # -- config <-> widgets ---------------------------------------------

    def _apply_config(self, config: Config) -> None:
        self.config_model = config
        set_value = self._set_input

        set_value("interval-hours", config.interval_hours)
        set_value("interval-minutes", config.interval_minutes)
        set_value("interval-seconds", config.interval_seconds)
        set_value("interval-ms", config.interval_ms)
        set_value("repeat-count", config.repeat_times)
        set_value("pos-x", config.x)
        set_value("pos-y", config.y)
        set_value("idle-delay", config.idle_delay)
        set_value("timer-hours", config.timer_hours)
        set_value("timer-minutes", config.timer_minutes)
        set_value("timer-seconds", config.timer_seconds)
        set_value("keeper-interval-minutes", config.keeper_interval_minutes)
        set_value("keeper-interval-seconds", config.keeper_interval_seconds)
        set_value("keeper-idle-delay", config.keeper_idle_delay)
        set_value("keeper-timer-hours", config.keeper_timer_hours)
        set_value("keeper-timer-minutes", config.keeper_timer_minutes)
        set_value("keeper-timer-seconds", config.keeper_timer_seconds)

        self._select_radio("#button-set", f"button-{config.button}")
        self._select_radio("#repeat-set", f"repeat-{config.repeat_mode}")
        self._select_radio("#location-set", f"location-{config.location_mode}")
        self._select_radio("#keeper-method-set", f"keeper-method-{config.keeper_method}")
        self._select_radio("#keeper-key-set", f"keeper-key-{config.keeper_key}")

        self._q("#idle-switch", Switch).value = config.idle_enabled
        self._q("#timer-switch", Switch).value = config.timer_enabled
        self._q("#hotkey-toggle", Input).value = config.hotkey_toggle
        self._q("#hotkey-stop", Input).value = config.hotkey_stop
        self._q("#hotkey-keeper", Input).value = config.hotkey_keeper
        self._q("#lock-switch", Switch).value = config.stop_on_lock
        self._q("#keeper-idle-switch", Switch).value = config.keeper_only_when_idle
        self._q("#keeper-timer-switch", Switch).value = config.keeper_timer_enabled

        self._sync_enabled_state()

    def _set_input(self, widget_id: str, value) -> None:
        self._q(f"#{widget_id}", Input).value = str(value)

    def _select_radio(self, selector: str, button_id: str) -> None:
        radio_set = self._q(selector, RadioSet)
        for button in radio_set.query(RadioButton):
            button.value = button.id == button_id

    def _radio_choice(self, selector: str, prefix: str, default: str) -> str:
        radio_set = self._q(selector, RadioSet)
        pressed = radio_set.pressed_button
        if pressed is None or not pressed.id:
            return default
        return pressed.id[len(prefix) :]

    def _collect_config(self) -> Config:
        config = Config(
            interval_hours=_int_value(self._q("#interval-hours", Input)),
            interval_minutes=_int_value(self._q("#interval-minutes", Input)),
            interval_seconds=_int_value(self._q("#interval-seconds", Input)),
            interval_ms=_int_value(self._q("#interval-ms", Input)),
            button=self._radio_choice("#button-set", "button-", "left"),
            repeat_mode=self._radio_choice("#repeat-set", "repeat-", "forever"),
            repeat_times=_int_value(self._q("#repeat-count", Input), 100),
            location_mode=self._radio_choice("#location-set", "location-", "cursor"),
            x=_int_value(self._q("#pos-x", Input)),
            y=_int_value(self._q("#pos-y", Input)),
            idle_enabled=self._q("#idle-switch", Switch).value,
            idle_delay=_float_value(self._q("#idle-delay", Input), 1.0),
            timer_enabled=self._q("#timer-switch", Switch).value,
            timer_hours=_int_value(self._q("#timer-hours", Input)),
            timer_minutes=_int_value(self._q("#timer-minutes", Input)),
            timer_seconds=_int_value(self._q("#timer-seconds", Input)),
            hotkey_toggle=(self._q("#hotkey-toggle", Input).value or "f6").strip(),
            hotkey_stop=(self._q("#hotkey-stop", Input).value or "f7").strip(),
            hotkey_keeper=(self._q("#hotkey-keeper", Input).value or "f8").strip(),
            stop_on_lock=self._q("#lock-switch", Switch).value,
            keeper_method=self._radio_choice("#keeper-method-set", "keeper-method-", "key"),
            keeper_key=self._radio_choice("#keeper-key-set", "keeper-key-", "f15"),
            keeper_interval_minutes=_int_value(self._q("#keeper-interval-minutes", Input)),
            keeper_interval_seconds=_int_value(self._q("#keeper-interval-seconds", Input)),
            keeper_only_when_idle=self._q("#keeper-idle-switch", Switch).value,
            keeper_idle_delay=_float_value(self._q("#keeper-idle-delay", Input), 30.0),
            keeper_timer_enabled=self._q("#keeper-timer-switch", Switch).value,
            keeper_timer_hours=_int_value(self._q("#keeper-timer-hours", Input)),
            keeper_timer_minutes=_int_value(self._q("#keeper-timer-minutes", Input)),
            keeper_timer_seconds=_int_value(self._q("#keeper-timer-seconds", Input)),
        )
        self.config_model = config
        return config

    def _sync_enabled_state(self) -> None:
        """Grey out fields that do not apply to the current selections."""
        fixed = self._radio_choice("#location-set", "location-", "cursor") == "fixed"
        for widget_id in ("#pos-x", "#pos-y"):
            self._q(widget_id, Input).disabled = not fixed
        self._q("#btn-pick", Button).disabled = not fixed

        counted = self._radio_choice("#repeat-set", "repeat-", "forever") == "times"
        self._q("#repeat-count", Input).disabled = not counted

        timer_on = self._q("#timer-switch", Switch).value
        for widget_id in ("#timer-hours", "#timer-minutes", "#timer-seconds"):
            self._q(widget_id, Input).disabled = not timer_on

        idle_on = self._q("#idle-switch", Switch).value
        self._q("#idle-delay", Input).disabled = not idle_on

        method = self._radio_choice("#keeper-method-set", "keeper-method-", "key")
        self._q("#keeper-key-set", RadioSet).disabled = method == "mouse"

        keeper_idle_on = self._q("#keeper-idle-switch", Switch).value
        self._q("#keeper-idle-delay", Input).disabled = not keeper_idle_on

        keeper_timer_on = self._q("#keeper-timer-switch", Switch).value
        for widget_id in (
            "#keeper-timer-hours",
            "#keeper-timer-minutes",
            "#keeper-timer-seconds",
        ):
            self._q(widget_id, Input).disabled = not keeper_timer_on

        self._refresh_static_hints()

    def _refresh_static_hints(self) -> None:
        config = self._collect_config()

        interval = config.interval
        rate = 1 / interval if interval else 0
        self._q("#interval-summary", Static).update(
            f"Every {_format_duration(interval) if interval >= 1 else f'{interval * 1000:.0f}ms'}"
            f"  (~{rate:,.1f} clicks/sec)"
        )

        if config.location_mode == "fixed":
            self._q("#location-hint", Static).update(
                f"Clicking at ({config.x}, {config.y})."
            )
        else:
            self._q("#location-hint", Static).update(
                "Clicks land wherever your pointer happens to be."
            )

        self._q("#idle-status", Static).update(
            "Idle mode is on." if config.idle_enabled else "Idle mode is off."
        )
        if config.timer_enabled:
            self._q("#timer-status", Static).update(
                f"Will stop after {_format_duration(config.timer_duration)}."
            )
        else:
            self._q("#timer-status", Static).update("Timer is off.")

        self._q("#lock-status", Static).update(self._lock_hint(config))

        nudge = {
            "key": f"Taps {config.keeper_key.replace('_', ' ').title()}",
            "mouse": "Moves the pointer 1px and back",
            "both": f"Taps {config.keeper_key.replace('_', ' ').title()} and nudges the pointer",
        }.get(config.keeper_method, "Taps a key")
        self._q("#keeper-interval-summary", Static).update(
            f"{nudge} every {_format_duration(config.keeper_interval)}."
        )

        if config.keeper_timer_enabled:
            self._q("#keeper-timer-status", Static).update(
                f"Will stop after {_format_duration(config.keeper_timer_duration)}."
            )
        else:
            self._q("#keeper-timer-status", Static).update(
                "Timer is off - it will keep going until you stop it."
            )

    def _lock_hint(self, config: Config) -> str:
        """Explain what this machine can actually detect."""
        if not config.stop_on_lock:
            return "Clicking continues through a lock; a suspend still stops it."

        support = session.lock_support()
        if support:
            return f"Lock detected via {support}. A suspend always stops the run."
        return (
            "[yellow]This machine offers no way to detect a screen lock, so only "
            "a suspend will stop the run.[/yellow]"
        )

    # -- hotkeys ---------------------------------------------------------

    def _register_hotkeys(self) -> None:
        config = self._collect_config()
        status = self._q("#hotkey-status", Static)

        # First binding wins if a user maps two actions onto one combination.
        wanted = [
            (config.hotkey_toggle, self._hotkey_toggle),
            (config.hotkey_stop, self._hotkey_stop),
            (config.hotkey_keeper, self._hotkey_keeper),
        ]
        bindings = {}
        seen = set()
        for spec, handler in wanted:
            try:
                key = hotkeys.normalize(spec)
            except hotkeys.HotkeyError:
                key = spec
            if key in seen:
                continue
            seen.add(key)
            bindings[spec] = handler

        error = self.hotkey_manager.register(bindings)
        keeper_status = self._q("#keeper-status", Static)
        if error:
            message = f"Global hotkeys unavailable: {error}"
            status.update(message)
            keeper_status.update(message)
        else:
            status.update(
                f"{hotkeys.display(config.hotkey_toggle)} starts and stops clicking, "
                f"{hotkeys.display(config.hotkey_stop)} stops everything."
            )
            keeper_status.update(
                f"{hotkeys.display(config.hotkey_keeper)} toggles this from any window."
            )

    def _hotkey_toggle(self) -> None:
        self.call_from_thread(self.action_toggle)

    def _hotkey_stop(self) -> None:
        self.call_from_thread(self.action_stop_all)

    def _hotkey_keeper(self) -> None:
        self.call_from_thread(self.action_keeper_toggle)

    def _suspend_hotkeys(self) -> None:
        self.hotkey_manager.unregister()
        self._suspended_hotkeys = True

    def _resume_hotkeys(self) -> None:
        if self._suspended_hotkeys:
            self._suspended_hotkeys = False
            self._register_hotkeys()

    # -- running ---------------------------------------------------------

    def action_toggle(self) -> None:
        if self.engine.running:
            self.action_stop()
        else:
            self.action_start()

    def action_start(self) -> None:
        config = self._collect_config()
        # The two modes fight each other: the keeper's nudges would read as
        # user activity and hold an idle-mode run back indefinitely.
        if self.keeper.running:
            self.keeper.stop()
            self.notify("Stopped staying online first.", severity="warning")
        problems = self.engine.start(config)
        if problems:
            self.notify("\n".join(problems), severity="error", title="Cannot start")
            return
        config.save(LAST_SESSION)
        self._q("#btn-start", Button).label = "Stop"
        self._q("#btn-stop", Button).disabled = False

    def action_stop(self) -> None:
        self.engine.stop()

    def action_stop_all(self) -> None:
        self.engine.stop()
        self.keeper.stop()

    def action_keeper_toggle(self) -> None:
        if self.keeper.running:
            self.keeper.stop()
        else:
            self.action_keeper_start()

    def action_keeper_stop(self) -> None:
        self.keeper.stop()

    def action_keeper_start(self) -> None:
        config = self._collect_config()
        if self.engine.running:
            self.engine.stop()
            self.notify("Stopped clicking first.", severity="warning")
        problems = self.keeper.start(config)
        if problems:
            self.notify("\n".join(problems), severity="error", title="Cannot start")
            return
        config.save(LAST_SESSION)
        self._q("#btn-keeper-start", Button).label = "Stop staying online"
        self._q("#btn-keeper-stop", Button).disabled = False

    def action_save_session(self) -> None:
        self._collect_config().save(LAST_SESSION)
        self.notify("Settings saved.", title="Saved")

    def action_show_help(self) -> None:
        self._q("#tabs", TabbedContent).active = "tab-help"

    def _on_engine_stop(self, reason: StopReason, clicks: int, error: str) -> None:
        """Called on the worker thread when the click loop ends."""
        self.call_from_thread(self._handle_engine_stop, reason, clicks, error)

    def _handle_engine_stop(self, reason: StopReason, clicks: int, error: str) -> None:
        if self._status_line is None or not self._status_line.is_attached:
            return
        self._q("#btn-start", Button).label = "Start"
        self._q("#btn-stop", Button).disabled = True

        if reason is StopReason.TIMER:
            self.notify(
                f"Timer elapsed after {clicks:,} clicks.", title="Stopped", severity="warning"
            )
        elif reason is StopReason.COUNT:
            self.notify(f"Finished {clicks:,} clicks.", title="Done")
        elif reason is StopReason.LOCKED:
            self.notify(
                f"Screen locked, so clicking stopped after {clicks:,} clicks.",
                title="Stopped",
                severity="warning",
            )
        elif reason is StopReason.SUSPENDED:
            self.notify(
                f"The machine suspended, so clicking stopped after {clicks:,} clicks.",
                title="Stopped",
                severity="warning",
            )
        elif reason is StopReason.ERROR:
            self.notify(error or "Unknown error", title="Stopped", severity="error")

    def _on_keeper_stop(self, reason: StopReason, nudges: int, error: str) -> None:
        """Called on the keeper's worker thread when it ends."""
        self.call_from_thread(self._handle_keeper_stop, reason, nudges, error)

    def _handle_keeper_stop(self, reason: StopReason, nudges: int, error: str) -> None:
        if self._status_line is None or not self._status_line.is_attached:
            return
        self._q("#btn-keeper-start", Button).label = "Start staying online"
        self._q("#btn-keeper-stop", Button).disabled = True

        if reason is StopReason.TIMER:
            self.notify(
                f"Timer elapsed after {nudges:,} nudges - you will go Away shortly.",
                title="Stopped",
                severity="warning",
            )
        elif reason is StopReason.SUSPENDED:
            self.notify(
                "The machine suspended, so staying online stopped.",
                title="Stopped",
                severity="warning",
            )
        elif reason is StopReason.ERROR:
            self.notify(error or "Unknown error", title="Stopped", severity="error")

    def _tick(self) -> None:
        """Refresh the status bar; runs on the UI thread at 10Hz."""
        status = self._status_line
        if status is None or not status.is_attached:
            return  # shutting down
        engine = self.engine

        if self.keeper.running:
            status.update("   ".join(self._keeper_status_parts()))
            return

        if not engine.running:
            hotkey = hotkeys.display(self.config_model.hotkey_toggle)
            status.update(f"[dim]Idle[/dim]   Press [b]{hotkey}[/b] or Start to begin.")
            return

        parts = []
        if engine.state is State.WAITING:
            parts.append("[yellow]Paused - waiting for you to be idle[/yellow]")
        else:
            parts.append("[green]Clicking[/green]")

        if engine.target_clicks:
            parts.append(f"{engine.click_count:,} / {engine.target_clicks:,}")
        else:
            parts.append(f"{engine.click_count:,} clicks")

        remaining = engine.remaining
        if remaining is not None:
            parts.append(f"stops in {_format_duration(remaining)}")

        status.update("   ".join(parts))

    def _keeper_status_parts(self) -> list[str]:
        keeper = self.keeper
        parts = []
        if keeper.state is State.WAITING:
            parts.append("[yellow]Staying online - you are active, nothing to do[/yellow]")
        else:
            parts.append("[green]Staying online[/green]")

        parts.append(f"{keeper.nudge_count:,} nudges")
        remaining = keeper.remaining
        if remaining is not None:
            parts.append(f"stops in {_format_duration(remaining)}")
        return parts

    # -- profiles ---------------------------------------------------------

    def refresh_profiles(self) -> None:
        option_list = self._q("#profile-list", OptionList)
        option_list.clear_options()
        names = list_profiles()
        if names:
            option_list.add_options(names)
        else:
            option_list.add_option("(no saved profiles yet)")

    def _selected_profile(self) -> str | None:
        option_list = self._q("#profile-list", OptionList)
        index = option_list.highlighted
        names = list_profiles()
        if index is None or not names or index >= len(names):
            return None
        return names[index]

    # -- events -----------------------------------------------------------

    @on(Button.Pressed, "#btn-start")
    def _press_start(self) -> None:
        self.action_toggle()

    @on(Button.Pressed, "#btn-stop")
    def _press_stop(self) -> None:
        self.action_stop()

    @on(Button.Pressed, "#btn-keeper-start")
    def _press_keeper_start(self) -> None:
        self.action_keeper_toggle()

    @on(Button.Pressed, "#btn-keeper-stop")
    def _press_keeper_stop(self) -> None:
        self.action_keeper_stop()

    @on(Button.Pressed, "#btn-record-keeper")
    def _record_keeper(self) -> None:
        self._record_hotkey("#hotkey-keeper")

    @on(RadioSet.Changed)
    @on(Switch.Changed)
    def _selection_changed(self) -> None:
        self._sync_enabled_state()

    @on(Input.Changed, ".number")
    def _number_changed(self) -> None:
        self._refresh_static_hints()

    @on(Input.Submitted, ".hotkey-input")
    @on(Input.Blurred, ".hotkey-input")
    def _hotkey_typed(self, event: Input.Changed) -> None:
        raw = (event.input.value or "").strip()
        if not raw:
            return
        try:
            event.input.value = hotkeys.normalize(raw)
        except hotkeys.HotkeyError as exc:
            self.notify(str(exc), severity="error", title="Invalid hotkey")
            return
        self._register_hotkeys()

    @on(Button.Pressed, "#btn-pick")
    def _press_pick(self) -> None:
        self._suspend_hotkeys()

        def done(result) -> None:
            self._resume_hotkeys()
            if result is None:
                return
            x, y = result
            self._set_input("pos-x", x)
            self._set_input("pos-y", y)
            self._refresh_static_hints()
            self.notify(f"Target set to ({x}, {y}).", title="Location picked")

        self.push_screen(LocationCaptureScreen(), done)

    @on(Button.Pressed, "#btn-record-toggle")
    def _record_toggle(self) -> None:
        self._record_hotkey("#hotkey-toggle")

    @on(Button.Pressed, "#btn-record-stop")
    def _record_stop(self) -> None:
        self._record_hotkey("#hotkey-stop")

    def _record_hotkey(self, selector: str) -> None:
        self._suspend_hotkeys()

        def done(combo: str | None) -> None:
            if combo is not None:
                try:
                    self._q(selector, Input).value = hotkeys.normalize(combo)
                except hotkeys.HotkeyError as exc:
                    self.notify(str(exc), severity="error", title="Invalid hotkey")
            self._resume_hotkeys()

        self.push_screen(HotkeyCaptureScreen(), done)

    @on(Button.Pressed, "#btn-profile-save")
    def _profile_save(self) -> None:
        name_input = self._q("#profile-name", Input)
        raw = (name_input.value or "").strip()
        if not raw:
            self.notify("Give the profile a name first.", severity="warning")
            return
        name = safe_name(raw)
        if name == LAST_SESSION:
            self.notify(f"'{LAST_SESSION}' is reserved.", severity="error")
            return
        self._collect_config().save(name)
        name_input.value = ""
        self.refresh_profiles()
        self.notify(f"Saved profile '{name}'.", title="Saved")

    @on(Button.Pressed, "#btn-profile-load")
    def _profile_load(self) -> None:
        name = self._selected_profile()
        if name is None:
            self.notify("Select a profile first.", severity="warning")
            return
        if self.engine.running or self.keeper.running:
            self.notify("Stop the current run before loading a profile.", severity="warning")
            return
        self._apply_config(Config.load(name))
        self._register_hotkeys()
        self.notify(f"Loaded '{name}'.", title="Loaded")

    @on(Button.Pressed, "#btn-profile-delete")
    def _profile_delete(self) -> None:
        name = self._selected_profile()
        if name is None:
            self.notify("Select a profile first.", severity="warning")
            return

        def done(confirmed: bool) -> None:
            if not confirmed:
                return
            try:
                profile_path(name).unlink()
            except OSError as exc:
                self.notify(str(exc), severity="error", title="Could not delete")
                return
            self.refresh_profiles()
            self.notify(f"Deleted '{name}'.", title="Deleted")

        self.push_screen(ConfirmScreen(f"Delete profile '{name}'?", "Delete"), done)


HELP_TEXT = f"""\
[b]Advanced Auto Clicker {__version__}[/b]

[b]Getting started[/b]
  1. Set how often to click on the [b]Clicking[/b] tab.
  2. Choose the mouse button, how many clicks, and where they land.
  3. Press [b]Start[/b], or use the global hotkey from any window.

[b]Global hotkeys[/b]
  Default [b]F6[/b] starts and stops clicking, [b]F7[/b] stops everything, and
  [b]F8[/b] toggles staying online. They fire even when this terminal is in the
  background, which is the whole point of them. Rebind them on the
  [b]Advanced[/b] and [b]Stay online[/b] tabs with the Record buttons.

[b]In-app keys[/b]
  Ctrl+R  start / stop        Ctrl+S  save settings
  Ctrl+T  stay online         Ctrl+Q  quit
  F1      this help
  Tab / Shift+Tab move between fields, arrow keys change radio buttons.

[b]Idle mode[/b]
  Holds clicks back while you are actually using the machine, and resumes
  after you have been still for the resume delay. Useful when you want the
  clicker to work only while you are away from the keyboard.

[b]Auto-stop timer[/b]
  Stops the run after a fixed duration so nothing is left clicking forever.
  Both the clicker and the presence keeper have one.

[b]Stay online[/b]
  Microsoft Teams (and Slack, and the rest) decide you are Away from the
  operating system's idle timer, not from what their window is doing. The
  [b]Stay online[/b] tab taps a harmless key - F15 by default, which no real
  keyboard sends - every minute or so, which resets that timer and keeps you
  Available. Leave "only when idle" on and it stands aside whenever you are
  genuinely typing. It keeps running while your screen is locked, which is
  usually the point, but stops if the machine suspends.

[b]Locking and suspending[/b]
  Clicking stops when the screen locks or the machine suspends, so you never
  come back to a run that carried on without you. Turn it off under
  "When the session goes away" on the [b]Advanced[/b] tab. Suspends are always
  caught; lock detection needs support from the desktop, and that tab tells
  you whether this machine has it.

[b]Profiles[/b]
  Whatever you had set is restored next launch automatically. Named profiles
  are for keeping several different setups around.

[b]Platform notes[/b]
  [b]macOS[/b]  Grant Accessibility permission to your terminal under
          System Settings > Privacy & Security > Accessibility, otherwise
          the OS silently blocks synthetic clicks.
  [b]Linux[/b]  Requires an X11/Xorg session. Wayland blocks applications from
          synthesizing input, so the clicker cannot work there.
  [b]Windows[/b] Works as-is, no admin rights needed.
"""
