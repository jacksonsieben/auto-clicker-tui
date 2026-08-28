"""Global hotkeys, using pynput so they fire while the terminal is unfocused."""

from __future__ import annotations

import threading

from . import backend

MODIFIER_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "altgr": "alt",
    "shift": "shift",
    "cmd": "cmd",
    "command": "cmd",
    "super": "cmd",
    "win": "cmd",
    "meta": "cmd",
}

MODIFIER_ORDER = ["ctrl", "alt", "shift", "cmd"]

# Keys pynput expects to be wrapped in angle brackets.
NAMED_KEYS = {
    "alt", "alt_gr", "alt_l", "alt_r", "backspace", "caps_lock", "cmd", "cmd_l",
    "cmd_r", "ctrl", "ctrl_l", "ctrl_r", "delete", "down", "end", "enter", "esc",
    "home", "insert", "left", "media_next", "media_play_pause", "media_previous",
    "media_volume_down", "media_volume_mute", "media_volume_up", "menu",
    "num_lock", "page_down", "page_up", "pause", "print_screen", "right",
    "scroll_lock", "shift", "shift_l", "shift_r", "space", "tab", "up",
} | {f"f{i}" for i in range(1, 21)}

# Friendlier spellings users are likely to type.
KEY_ALIASES = {
    "escape": "esc",
    "return": "enter",
    "pgup": "page_up",
    "pgdn": "page_down",
    "pagedown": "page_down",
    "pageup": "page_up",
    "del": "delete",
    "ins": "insert",
    "capslock": "caps_lock",
    "printscreen": "print_screen",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
}


class HotkeyError(ValueError):
    """Raised when a hotkey string cannot be understood."""


def normalize(spec: str) -> str:
    """Canonicalise a hotkey string, e.g. ``"Ctrl + Shift+A"`` -> ``"ctrl+shift+a"``."""
    if not spec or not spec.strip():
        raise HotkeyError("Hotkey cannot be empty")

    parts = [p.strip().lower() for p in spec.replace("<", "").replace(">", "").split("+")]
    parts = [p for p in parts if p]
    if not parts:
        raise HotkeyError("Hotkey cannot be empty")

    modifiers: list[str] = []
    key: str | None = None
    for part in parts:
        if part in MODIFIER_ALIASES:
            modifier = MODIFIER_ALIASES[part]
            if modifier not in modifiers:
                modifiers.append(modifier)
            continue
        if key is not None:
            raise HotkeyError(f"'{spec}' has more than one non-modifier key")
        key = KEY_ALIASES.get(part, part)

    if key is None:
        raise HotkeyError(f"'{spec}' is only modifiers - add a regular key")
    if len(key) != 1 and key not in NAMED_KEYS:
        raise HotkeyError(f"Unknown key '{key}'")

    ordered = [m for m in MODIFIER_ORDER if m in modifiers]
    return "+".join([*ordered, key])


def to_pynput(spec: str) -> str:
    """Convert a canonical hotkey into pynput's ``<ctrl>+<shift>+a`` syntax."""
    parts = normalize(spec).split("+")
    return "+".join(f"<{p}>" if len(p) != 1 else p for p in parts)


def display(spec: str) -> str:
    """Render a hotkey for the UI, e.g. ``ctrl+shift+a`` -> ``Ctrl+Shift+A``."""
    try:
        parts = normalize(spec).split("+")
    except HotkeyError:
        return spec.upper()
    return "+".join(p.upper() if len(p) == 1 else p.replace("_", " ").title() for p in parts)


def key_to_name(key) -> str | None:
    """Turn a pynput key event into a canonical key name."""
    if backend.keyboard is None:
        return None

    if isinstance(key, backend.keyboard.Key):
        name = key.name
        for prefix in ("ctrl", "alt", "shift", "cmd"):
            if name.startswith(prefix):
                return MODIFIER_ALIASES.get(prefix, prefix)
        return name

    char = getattr(key, "char", None)
    if char:
        return char.lower()

    # Ctrl+<letter> arrives as an unprintable control character.
    vk = getattr(key, "vk", None)
    if vk is not None and 65 <= vk <= 90:
        return chr(vk).lower()
    return None


class HotkeyManager:
    """Registers a set of ``{hotkey: callback}`` bindings globally."""

    def __init__(self) -> None:
        self._listener = None
        self.error = ""

    @property
    def active(self) -> bool:
        return self._listener is not None

    def register(self, bindings: dict[str, callable]) -> str:
        """(Re)register bindings. Returns an error message, or "" on success."""
        self.unregister()
        self.error = ""

        if not backend.AVAILABLE:
            self.error = backend.UNAVAILABLE_REASON or "Input backend unavailable"
            return self.error

        try:
            mapping = {to_pynput(spec): cb for spec, cb in bindings.items()}
        except HotkeyError as exc:
            self.error = str(exc)
            return self.error

        try:
            self._listener = backend.keyboard.GlobalHotKeys(mapping)
            self._listener.start()
        except Exception as exc:
            self._listener = None
            self.error = str(exc) or exc.__class__.__name__
            return self.error
        return ""

    def unregister(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None


class HotkeyRecorder:
    """Captures the next key combination the user presses, globally.

    Global capture (rather than reading the terminal) is what lets users bind
    F-keys and modifier combos the terminal would otherwise swallow.
    """

    def __init__(self, on_captured, on_cancelled=None) -> None:
        self._on_captured = on_captured
        self._on_cancelled = on_cancelled
        self._listener = None
        self._modifiers: list[str] = []
        self._done = threading.Event()

    def start(self) -> str:
        if not backend.AVAILABLE:
            return backend.UNAVAILABLE_REASON or "Input backend unavailable"
        try:
            self._listener = backend.keyboard.Listener(
                on_press=self._on_press, on_release=self._on_release
            )
            self._listener.start()
        except Exception as exc:
            return str(exc) or exc.__class__.__name__
        return ""

    def cancel(self) -> None:
        if self._done.is_set():
            return
        self._done.set()
        self._stop_listener()
        if self._on_cancelled is not None:
            self._on_cancelled()

    def _stop_listener(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass

    def _on_press(self, key) -> None:
        if self._done.is_set():
            return
        name = key_to_name(key)
        if name is None:
            return

        if name in MODIFIER_ORDER:
            if name not in self._modifiers:
                self._modifiers.append(name)
            return

        if name == "esc" and not self._modifiers:
            self.cancel()
            return

        ordered = [m for m in MODIFIER_ORDER if m in self._modifiers]
        combo = "+".join([*ordered, name])
        self._done.set()
        self._stop_listener()
        self._on_captured(combo)

    def _on_release(self, key) -> None:
        name = key_to_name(key)
        if name in self._modifiers:
            self._modifiers.remove(name)
