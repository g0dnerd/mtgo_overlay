import sys
import os
import configparser
import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.simpledialog as sd
import win32gui
import win32con
import time
import logging
from pystray import Icon, MenuItem, Menu
from PIL import Image
from watchdog.events import LoggingEventHandler
from watchdog.observers import Observer
import crawler.logs as logs
import image_recognition.rec as rc
from capture.screen_capture import capture_mtgo
from crawler.fetch import get_card_ratings
from data.resources import resource_path

def make_window_click_through(hwnd):
    """Uses the win32 API to make a window fully transparent, always on-top and click-through."""
    styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)
    
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)

class RatingOverlay:
    def __init__(self):
        base_path = resource_path('')
        logging.info(f'Operating from base path: {base_path}')
        log_path = resource_path('debug.log')
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)s - %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S',
                            handlers=[
                                logging.FileHandler(log_path),
                                logging.StreamHandler(sys.stdout)
                            ])

        self.screengrab, self.screen = capture_mtgo()

        self.root = tk.Tk()
        self.root.bind('<<SafeDestroy>>', self.safe_destroy)
        self.root.withdraw()

        self.labels = []
        self.pack = []

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

        self.config = configparser.ConfigParser()
        self.ini_path = os.path.expanduser('~') + '/Documents\\settings.ini'
        if not os.path.exists(self.ini_path):
            with open(self.ini_path, 'w') as f:
                f.close()
        self.load_config()

        self.setup_tray_icon()
        logging.info('Successfully initialized overlay.')
        self.root.mainloop()

    def load_config(self):
        self.config.read(self.ini_path)
        logging.info(f'Loaded user config from {self.ini_path}')
        mtgo_user = self.config.get('Settings', 'Username', fallback='')
        self.set_user(mtgo_user, save=False)
        log_path = self.config.get('Settings', 'LogPath', fallback=os.path.expanduser('~'))
        self.update_log_path(log_path, save=False)

    def save_config(self):
        if not self.config.has_section('Settings'):
            self.config.add_section('Settings')
        self.config.set('Settings', 'Username', self.mtgo_user)
        self.config.set('Settings', 'LogPath', self.log_path)
        with open(self.ini_path, 'w') as configfile:
            self.config.write(configfile)
        logging.info(f'User config saved to {self.ini_path}')
        
    def setup_tray_icon(self):
        icon_path = 'tray.ico'
        icon_path = resource_path(icon_path)
        icon_image = Image.open(icon_path)
        menu = Menu(MenuItem('Enter MTGO username', self.open_username_dialog),
                    MenuItem('Change Log Folder', self.change_log_folder),
                    MenuItem('Exit', self.exit_application))
        self.icon = Icon("RatingOverlay", icon_image, "Rating Overlay", menu)
        self.icon.run_detached()

    def open_username_dialog(self):
        self.root.after(0, self.prompt_username)

    def prompt_username(self):
        username = sd.askstring("MTGO Username", "Enter your MTGO username:")

        self.set_user(username)

    def set_user(self, username, save=True):
        if username:
            self.mtgo_user = username
            logging.info(f'MTGO Username updated to: {self.mtgo_user}')
            if save:
                self.save_config()

    def change_log_folder(self):
        # Open a dialog to select a folder
        folder_selected = fd.askdirectory(title='Select Log Folder')
        if folder_selected:
            logging.info(f'Selected folder: {folder_selected}')
            self.update_log_path(folder_selected)

    def update_log_path(self, new_path, save=True):
        # Update the log path
        self.log_path, self.log_file_path = logs.get_current_log(new_path, self.mtgo_user)
        logging.info(f'Log path updated to: {self.log_path}\n\t\t\tNewest log file: {self.log_file_path}')
        self.draft_monitor_thread()
        if save:
            self.save_config()

    def exit_application(self):
        if self.log_observer:
            if self.log_observer.is_alive():
                self.log_observer.stop()
                self.log_observer.join()
        self.icon.stop()

        if self.root:
            self.root.event_generate('<<SafeDestroy>>', when='tail')

    def safe_destroy(self, event=None):
        self.root.quit()
        self.root.destroy()
        sys.exit()

    def draft_monitor_thread(self):
        # Thread to handle file monitoring and not block Tkinter's main loop
        self.thread = threading.Thread(target=self.monitor_for_draft)
        self.thread.setDaemon(True)
        self.thread.start()

    def monitor_for_draft(self):
        logging.info('Started monitoring thread.')
        event_handler = LoggingEventHandler()
        event_handler.on_created = lambda event:\
             self.log_monitor_thread(event.src_path) if event.event_type == 'created' and \
                logs.is_valid_draft(event.src_path, self.log_path, self.mtgo_user) else None
        self.draft_observer = Observer()
        self.draft_observer.schedule(event_handler, path=self.log_path, recursive=True)
        self.draft_observer.start()
        
        try:
            while self.draft_observer.is_alive():  # Keep waiting until the observer is alive.
                self.draft_observer.join(timeout=1)
        except KeyboardInterrupt:
            self.draft_observer.stop()

    def log_monitor_thread(self, path):
        self.log_file_path = path
        logging.info(f'Draft has started, monitoring at {self.log_file_path}.')
        self.draft_observer.stop()
        self.root.after(0, self.draft_observer.join)
        self.thread = threading.Thread(target=self.monitor_log_file)
        self.thread.setDaemon(True)
        self.thread.start()

    def monitor_log_file(self):
        event_handler = LoggingEventHandler()
        event_handler.on_modified = lambda event:\
            self.update_labels() if event.event_type == 'modified' and event.src_path == self.log_file_path else None
        self.log_observer = Observer()
        self.log_observer.schedule(event_handler, path=self.log_path, recursive=True)
        self.log_observer.start()
        
        try:
            while self.log_observer.is_alive():  # Keep waiting until the observer is alive.
                self.log_observer.join(timeout=1)
        except KeyboardInterrupt:
            self.log_observer.stop()

    def update_labels(self):
        logging.info('Log update detected.')
        # Get card names for the current pack on screen
        try:
            pack = logs.get_newest_pack(self.log_file_path)
        except ValueError as e:
            logging.warning(f'Unable to parse pack: {e}')
            return

        # Check if the pack has already been processed
        if pack == self.pack:
            return

        self.pack = pack
        logging.info(f'Updated pack to {self.pack}')

        # Clear old labels
        self.root.after(0, self.clear_labels)

        # If there is a new pack in the log, grab a new screenshot
        time.sleep(0.3)
        self.screengrab, self.screen = capture_mtgo()
        
        try:
            positions, names = rc.get_pos_and_names(self.screengrab, self.pack)
        except ValueError as e:
            logging.info(f'Error encountered while trying to find cards on screen: {e}')
            self.pack = []
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
        if not rating:
            label_text = 'N/A'
        else:
            label_text = f'GIH {rating}'
        label = tk.Label(label_window, text=f'{label_text}', font=('Arial', 11), fg='black')
        label.pack(fill=tk.BOTH, expand=True)
        hwnd = label_window.winfo_id()
        make_window_click_through(hwnd)
        self.labels.append(label_window)