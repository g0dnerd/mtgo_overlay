from mss import mss, tools
import pygetwindow as gw

def get_mtgo_window():
    windows = gw.getWindowsWithTitle('Draft League:')
    return windows[0] if windows else None

def capture_mtgo(mtgo_window):
    if mtgo_window:
        with mss() as sct:
            monitor = {
                'top': mtgo_window.top,
                'left': mtgo_window.left,
                'width': mtgo_window.width,
                'height': mtgo_window.height
            }
            screenshot = sct.grab(monitor)
            
            output = 'sct-{top}x{left}_{width}x{height}.png'.format(**monitor)
            tools.to_png(screenshot.rgb, screenshot.size, output=output)
            
            return output