from mss import mss
import pygetwindow as gw
import cv2
import numpy as np


def get_mtgo_window():
    draft_windows = gw.getWindowsWithTitle("Draft League")
    if not draft_windows:
        mtgo_windows = gw.getWindowsWithTitle("Magic: The Gathering Online")
        if not mtgo_windows:
            return None
        return mtgo_windows[0]
    return draft_windows[0]


def capture_mtgo():
    mtgo_window = get_mtgo_window()
    if mtgo_window is not None:
        with mss() as sct:
            monitor = {
                "top": mtgo_window.top,
                "left": mtgo_window.left,
                "width": mtgo_window.width,
                "height": mtgo_window.height,
            }
            try:
                screenshot = sct.grab(monitor)
            except AttributeError:
                pass

            img = np.array(screenshot)
            cv2_img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            return cv2_img, mtgo_window
    else:
        raise ValueError("No MTGO window found.")
