"""Capture MTGO's client area (Windows runtime).

Captures the *client* rect (not the whole window) in physical pixels via mss, so
recognition operates on exactly the area the overlay is pinned to. HWND discovery
is shared with :mod:`mtgo_overlay.system.win32`.
"""

from __future__ import annotations

import numpy as np

from ..system import win32


class CaptureError(RuntimeError):
    pass


def capture_client_area(hwnd: int) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Return ``(bgr_image, (left, top, width, height))`` for ``hwnd``'s client area.

    The image is physical-pixel BGR; the rect is in physical screen coordinates
    (the process is per-monitor DPI aware).
    """
    import cv2
    import mss

    left, top, width, height = win32.get_client_rect_on_screen(hwnd)
    if width <= 0 or height <= 0:
        raise CaptureError(f"MTGO client area has zero size: {(width, height)}")

    monitor = {"left": left, "top": top, "width": width, "height": height}
    with mss.mss() as sct:
        shot = sct.grab(monitor)
    bgra = np.asarray(shot)
    bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    return bgr, (left, top, width, height)


def find_and_capture() -> tuple[np.ndarray, tuple[int, int, int, int]]:
    hwnd = win32.find_mtgo_hwnd()
    if hwnd is None:
        raise CaptureError("No MTGO window found.")
    return capture_client_area(hwnd)
