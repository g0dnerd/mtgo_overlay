"""Windows window / DPI integration via ctypes (user32).

Import-safe everywhere: the module imports cleanly under WSL/Linux so the rest of
the package type-checks and the headless tests run. Every function that actually
touches the Win32 API raises :class:`RuntimeError` if called off-Windows, so a
mistaken call fails loudly instead of silently no-op'ing.

We use ctypes rather than pywin32 for geometry/DPI (fewer surprises across
pywin32 versions); pywin32 is still a runtime dep but optional here.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# Extended window styles.
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20

# DPI awareness context handles (negative pseudo-handles).
_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

# Candidate MTGO window titles, most specific first.
MTGO_TITLES: tuple[str, ...] = (
    "Draft League",
    "Magic: The Gathering Online",
    "Magic Online",
)

_prototypes_ready = False


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise RuntimeError("win32 helpers are only available on Windows")


def _user32():
    _require_windows()
    u = ctypes.windll.user32
    _configure_prototypes(u)
    return u


def _configure_prototypes(u) -> None:
    """Set restype/argtypes once so 64-bit pointer-sized returns are correct."""
    global _prototypes_ready
    if _prototypes_ready:
        return
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    u.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    _prototypes_ready = True


def set_dpi_awareness() -> None:
    """Make the process per-monitor DPI aware. Call BEFORE creating QApplication.

    Tries the modern V2 context, then the 8.1-era API, then the Vista fallback.
    No-op (silent) off Windows so ``run.py`` can call it unconditionally.
    """
    if not IS_WINDOWS:
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def set_click_through(hwnd: int) -> None:
    """Make ``hwnd`` click-through, non-activating, off the taskbar/alt-tab.

    Deliberately does NOT call ``SetLayeredWindowAttributes(LWA_ALPHA)`` - that
    forces a uniform alpha and fights Qt's per-pixel ``WA_TranslucentBackground``
    compositing.
    """
    u = _user32()
    styles = u.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    styles |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
    u.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, styles)


def get_client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int]:
    """Return MTGO's *client* area as ``(left, top, width, height)`` in screen px.

    Client (not window) origin means the overlay can be pinned to the inside of
    the frame; recognition coords are then a pure ``capture_px`` mapping.
    """
    u = _user32()
    rect = wintypes.RECT()
    if not u.GetClientRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetClientRect failed")
    origin = wintypes.POINT(rect.left, rect.top)
    if not u.ClientToScreen(hwnd, ctypes.byref(origin)):
        raise OSError("ClientToScreen failed")
    return (origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)


def get_foreground_hwnd() -> int | None:
    """HWND of the window the user is currently focused on, or ``None``."""
    u = _user32()
    hwnd = u.GetForegroundWindow()
    return int(hwnd) if hwnd else None


def find_mtgo_hwnd() -> int | None:
    """Find the MTGO (or 'Draft League') top-level window HWND, or ``None``."""
    u = _user32()
    matches: list[tuple[int, str]] = []

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def _callback(hwnd, _lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        length = u.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        lowered = title.lower()
        for rank, candidate in enumerate(MTGO_TITLES):
            if candidate.lower() in lowered:
                matches.append((rank, hwnd))
                break
        return True

    u.EnumWindows(_callback, 0)
    if not matches:
        return None
    matches.sort(key=lambda item: item[0])  # most-specific title wins
    return int(matches[0][1])
