from mss import mss
import pygetwindow as gw
import cv2
import numpy as np

def get_mtgo_window():
    windows = gw.getWindowsWithTitle('Magic: The Gathering Online')
    if not windows:
        windows = gw.getWindowsWithTitle('Draft League')
    return windows[0] if windows else None

def capture_mtgo():
    mtgo_window: gw._pygetwindow_win.Win32Window = get_mtgo_window()
    if mtgo_window:
        with mss() as sct:
            monitor = {
                'top': mtgo_window.top,
                'left': mtgo_window.left,
                'width': mtgo_window.width,
                'height': mtgo_window.height
            }
            screenshot = sct.grab(monitor)

            img = np.array(screenshot)
            cv2_img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            # cv2.imshow('screengrab', cv2_img)
            return cv2_img, mtgo_window
    else:
        raise ValueError('No MTGO window found.')