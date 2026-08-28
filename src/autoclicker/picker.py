"""Pick a screen coordinate by clicking anywhere."""

from __future__ import annotations

import threading

from . import backend


class LocationPicker:
    """Waits for one physical mouse click and reports where it landed.

    Escape cancels. The click is captured on release so the press that starts
    the pick cannot immediately satisfy it.
    """

    def __init__(self, on_picked, on_cancelled=None) -> None:
        self._on_picked = on_picked
        self._on_cancelled = on_cancelled
        self._mouse_listener = None
        self._key_listener = None
        self._done = threading.Event()

    def start(self) -> str:
        if not backend.AVAILABLE:
            return backend.UNAVAILABLE_REASON or "Input backend unavailable"
        try:
            self._mouse_listener = backend.mouse.Listener(on_click=self._on_click)
            self._mouse_listener.start()
            self._key_listener = backend.keyboard.Listener(on_press=self._on_key)
            self._key_listener.start()
        except Exception as exc:
            self._stop()
            return str(exc) or exc.__class__.__name__
        return ""

    def cancel(self) -> None:
        if self._done.is_set():
            return
        self._done.set()
        self._stop()
        if self._on_cancelled is not None:
            self._on_cancelled()

    def _stop(self) -> None:
        for attr in ("_mouse_listener", "_key_listener"):
            listener = getattr(self, attr)
            setattr(self, attr, None)
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass

    def _on_click(self, x, y, button, pressed) -> None:
        if pressed or self._done.is_set():
            return
        self._done.set()
        self._stop()
        self._on_picked(int(x), int(y))

    def _on_key(self, key) -> None:
        if backend.keyboard is not None and key == backend.keyboard.Key.esc:
            self.cancel()
