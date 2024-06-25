import threading
import tkinter as tk
import win32gui
import win32con
import time
import logging
from watchdog.events import LoggingEventHandler
from watchdog.observers import Observer
import image_recognition.logs as logs
import image_recognition.rec as rc
from capture.screen_capture import capture_mtgo
from crawler.ratings import get_card_ratings

def make_window_click_through(hwnd):
    styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)
    
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)

class RatingOverlay:
    def __init__(self):
        self.screengrab, self.screen = capture_mtgo()
        self.root = tk.Tk()
        self.labels = []
        self.pack = []
        self.log_path, self.log_file_path = logs.get_current_log('pjk_')
        # print(f'Folder Path: {self.log_path}, File Path: {self.log_file_path}')
        # self.log_path, self.log_file_path = 'C:\\Users\\paulk/Documents/', 'C:\\Users\\paulk/Documents/pjk_-2024.6.25-8265-29741267-MH3MH3MH3.txt'
        print(self.log_path)
        print(f'Monitoring {self.log_file_path}')

        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
        
        self.event_handler = LoggingEventHandler()
        self.event_handler.on_modified = lambda event:\
            self.update_labels() if event.event_type == 'modified' and event.src_path == self.log_file_path else\
                print(f'Unrelated event of type {event.event_type} at {event.src_path}')

        # Calculate dimensions
        width = self.screengrab.shape[1]
        height = self.screengrab.shape[0]
        screen_x = self.screen.topleft[0]
        screen_y = self.screen.topleft[1]
        self.root.geometry(f'{width}x{height}+{screen_x}+{screen_y}')

        # Make the window borderless and transparent
        self.root.attributes("-alpha", 0)
        self.root.wm_attributes("-topmost", 1)
        self.root.overrideredirect(True)
        
        make_window_click_through(self.root.winfo_id())
        
        # self.update_labels()
        self.setup_labels()
        self.root.mainloop()

    def setup_labels(self):
        # Thread to handle file monitoring and not block Tkinter's main loop
        thread = threading.Thread(target=self.monitor_log_file)
        thread.setDaemon(True)
        thread.start()

    def monitor_log_file(self):
        observer = Observer()
        observer.schedule(self.event_handler, path=self.log_path, recursive=True)
        observer.start()
        observer.join()

    def update_labels(self):
        
        # Get card names for the current pack on screen
        try:
            pack = logs.get_newest_pack(self.log_file_path)
        except ValueError as e:
            logging.warning(e)
            return

        # Check if the pack has already been processed
        if pack == self.pack:
            return

        # Clear old labels
        self.root.after(0, self.clear_labels)

        self.pack = pack
        print(f'Updated pack to {self.pack}')

        # If there is a new pack in the log, grab a new screenshot
        time.sleep(1.0)
        self.screengrab, self.screen = capture_mtgo()
        
        try:
            positions, names = rc.get_pos_and_names(self.screengrab, self.pack)
        except ValueError as e:
            print(f'Error caught: {e}')
            return

        ratings = get_card_ratings(names)
        for pos, rating in zip(positions, ratings):
            self.root.after(0, self.create_label, pos, rating)

    def clear_labels(self):
        for label in self.labels:
            label.destroy()
        self.labels.clear()

    def create_label(self, text_pos, rating):
        label_window = tk.Toplevel(self.root)
        label_window.geometry(f'75x30+{text_pos[0]}+{text_pos[1]}')
        label_window.overrideredirect(True)
        label_window.wm_attributes("-topmost", True)
        label = tk.Label(label_window, text=f'GIH {rating}', font=('Arial', 12), fg='black')
        label.pack(fill=tk.BOTH, expand=True)
        hwnd = label_window.winfo_id()
        make_window_click_through(hwnd)
        self.labels.append(label_window)