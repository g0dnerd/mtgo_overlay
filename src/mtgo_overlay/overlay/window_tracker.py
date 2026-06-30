"""Poll MTGO's client rect and signal moves / resizes (Windows runtime).

The old overlay positioned itself once at startup and never followed MTGO. This
QTimer (~10 Hz, UI thread) keeps the overlay pinned and triggers re-recognition
when the window is resized. The HWND lookup + rect read are injected so the
state machine is unit-testable off Windows.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ..system import win32

FindHwnd = Callable[[], "int | None"]
GetRect = Callable[[int], tuple[int, int, int, int]]


class WindowTracker(QObject):
    moved = Signal(int, int, int, int)    # x, y, w, h — position changed only
    resized = Signal(int, int, int, int)  # x, y, w, h — size changed (re-recognize)
    lost = Signal()                        # MTGO window disappeared

    def __init__(
        self,
        hz: float = 10.0,
        parent: QObject | None = None,
        *,
        find_hwnd: FindHwnd = win32.find_mtgo_hwnd,
        get_rect: GetRect = win32.get_client_rect_on_screen,
    ) -> None:
        super().__init__(parent)
        self._find_hwnd = find_hwnd
        self._get_rect = get_rect
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hz))
        self._timer.timeout.connect(self.poll)
        self._last: tuple[int, int, int, int] | None = None
        self.hwnd: int | None = None

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def poll(self) -> None:
        hwnd = self._find_hwnd()
        if hwnd is None:
            if self._last is not None:
                self.lost.emit()
            self._last = None
            self.hwnd = None
            return

        rect = self._get_rect(hwnd)
        if rect == self._last:
            return

        prev, self._last, self.hwnd = self._last, rect, hwnd
        x, y, w, h = rect
        if prev is None or (prev[2], prev[3]) != (w, h):
            self.resized.emit(x, y, w, h)  # appeared or resized -> re-recognize
        else:
            self.moved.emit(x, y, w, h)  # only moved -> just reposition
