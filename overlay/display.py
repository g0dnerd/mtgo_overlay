"""Holds the application itself and a helper method for making it click-through."""

import sys
import os
import time
import configparser
import threading
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.simpledialog as sd
from tkinter import font
import win32gui
import win32con
from pystray import Icon, MenuItem, Menu
from PIL import Image
from watchdog.events import LoggingEventHandler
from watchdog.observers import Observer
import image_recognition.rec as rc
import capture.screen_capture as cap
import crawler.logs as logs
import crawler.fetch as fetch
import data.resources as util


def make_window_click_through(hwnd):
    """Uses the win32 API to make a window fully transparent, always on-top and click-through."""
    styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    styles |= win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, styles)
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)


class RatingOverlay:
    """Represents the application and everything it holds."""

    def __init__(self):
        base_path = util.resource_path("")
        util.log_info(f"Operating from base path: {base_path}")

        self.root = tk.Tk()
        self.root.bind("<<SafeDestroy>>", self.safe_destroy)
        self.root.withdraw()

        try:
            self.screengrab, self.screen = cap.capture_mtgo()
        except ValueError as e:
            util.log_exception(e)
            util.log_warning('Shutting down in 5 seconds.')
            time.sleep(5.0)
            self.exit_application()
            return

        self.labels = []

        # Calculate dimensions
        width = self.screengrab.shape[1]
        height = self.screengrab.shape[0]
        screen_x = self.screen.topleft[0]
        screen_y = self.screen.topleft[1]
        self.root.geometry(f"{width}x{height}+{screen_x}+{screen_y}")

        # Make the window borderless and transparent
        self.root.attributes("-alpha", 0)
        self.root.wm_attributes("-topmost", 1)
        self.root.overrideredirect(True)

        make_window_click_through(self.root.winfo_id())

        self.config = configparser.ConfigParser()
        self.ini_path = os.path.expanduser("~") + "/Documents\\settings.ini"
        if not os.path.exists(self.ini_path):
            with open(self.ini_path, "w", encoding="utf-8") as f:
                f.close()

        self.log_path = ""
        self.log_file_path = ""
        self.mtgo_user = ""
        self.expansion = ""

        self.load_config()

        self.setup_tray_icon()
        util.log_info("Successfully initialized overlay.")
        self.root.mainloop()

    def load_config(self):
        """Attempts to load a settings configuration from an .ini file."""
        self.config.read(self.ini_path)
        util.log_info(f"Trying to load user config from {self.ini_path}")
        log_path = self.config.get(
            "Settings", "LogPath", fallback=os.path.expanduser("~")
        )

        mtgo_user = self.config.get("Settings", "Username", fallback="")
        try:
            self.set_user(mtgo_user, save=False)
        except ValueError as e:
            util.log_warning(e)
            self.log_path = ""
            return

        self.update_log_path(log_path, save=False)

    def save_config(self):
        """Writes the currently set configuration to an .ini file."""
        if not self.config.has_section("Settings"):
            self.config.add_section("Settings")
        self.config.set("Settings", "Username", self.mtgo_user)
        self.config.set("Settings", "LogPath", self.log_path)
        with open(self.ini_path, "w", encoding="utf-8") as configfile:
            self.config.write(configfile)
        util.log_info(f"User config saved to {self.ini_path}")

    def setup_tray_icon(self):
        """Sets up the tray icon with the menu choices and runs it."""
        icon_path = "tray.ico"
        icon_path = util.resource_path(icon_path)
        icon_image = Image.open(icon_path)

        menu = Menu(
            MenuItem("Reboot", self.reboot),
            MenuItem("Enter MTGO username", self.open_username_dialog),
            MenuItem("Change Log Folder", self.change_log_folder),
            MenuItem("Exit", self.exit_application),
        )
        self.icon = Icon("RatingOverlay", icon_image, "Rating Overlay", menu)
        self.icon.run_detached()

    def reboot(self):
        """Points the application at the newest log file and starts monitoring it."""
        try:
            if self.log_observer.is_alive():
                self.log_observer.stop()
                self.log_observer.join()
        except AttributeError:
            pass
        self.load_config()
        util.log_info("Restarting draft logging.")
        newest_log = logs.get_current_log(self.log_path, self.mtgo_user)
        self.root.after(0, self.start_log_monitor_thread, newest_log)

    def open_username_dialog(self):
        """Helper to call the username dialog without disrupting tkinter's mainloop."""
        self.root.after(0, self.prompt_username)

    def prompt_username(self):
        """Prompts the user to input their MTGO username."""
        username = sd.askstring("MTGO Username", "Enter your MTGO username:")

        self.set_user(username)

    def set_user(self, username, save=True):
        """Sets the username and writes to config."""
        if username:
            self.mtgo_user = username
            util.log_info(f"MTGO Username updated to: {self.mtgo_user}")
            if save:
                self.save_config()
        else:
            self.mtgo_user = ""
            raise ValueError(
                "Please use the tray menu to enter your username before starting a draft."
            )

    def finish_draft(self):
        """Finishes the current draft and puts the application to sleep."""
        try:
            if self.log_observer.is_alive():
                self.log_observer.stop()
                self.log_observer.join()
        except AttributeError:
            pass
        util.log_info("Draft finished. Waiting for new draft.")
        self.root.after(0, self.start_draft_monitor_thread)

    def change_log_folder(self):
        """Presents the directory browser for the user to select the log folder path."""
        # Open a dialog to select a folder
        folder_selected = fd.askdirectory(title="Select Log Folder")
        if folder_selected:
            util.log_info(f"Selected folder: {folder_selected}")
            self.update_log_path(folder_selected)

    def update_log_path(self, new_path, save=True):
        """Updates the log folder path and writes to config."""
        # Update the log path
        self.log_path = new_path
        util.log_info(f"Log path updated to: {self.log_path}")
        self.start_draft_monitor_thread()
        if save:
            self.save_config()

    def exit_application(self):
        """Safely exits the application."""
        try:
            if self.log_observer.is_alive():
                self.log_observer.stop()
                self.log_observer.join()
        except AttributeError:
            pass
        try:
            self.icon.stop()
        except AttributeError:
            pass

        if self.root:
            self.root.event_generate("<<SafeDestroy>>", when="tail")

    def safe_destroy(self, event=None):
        """Cleans up the tkinter root window."""
        self.root.quit()
        self.root.destroy()
        sys.exit()

    def ensure_data(self, expansion):
        """Ensures the data for the MTGO-Draft format 3x{expansion} exists.
        Exits the application if the resource path does not exist at all.
        Attempts to convert CSV data to JSON if JSON data is missing.
        """

        # Ensure bulk data exists
        bulk_path = "bulk_data.json"
        bulk_path = util.resource_path(bulk_path)
        if not os.path.isfile(bulk_path):
            fetch.update_bulk_data()

        # Ensure the base path for the draft set exists
        if not os.path.exists(util.resource_path(expansion)):
            util.log_info(f"Format: {expansion}")
            util.log_exception(
                f"Fatal: Resource path for format {expansion} does not exist. Exiting."
            )
            self.exit_application()

        # Ensure card variant data exists
        variants_path = util.resource_path(f"{expansion}/card_variants.json")
        if not os.path.isfile(variants_path):
            fetch.cache_variants(expansion)

        # Ensure image data is cached
        if not os.path.exists(util.resource_path(f"{expansion}/images")):
            ids = fetch.get_all_ids_for_set(expansion)
            fetch.cache_cards_by_id(ids)

        # Ensure rating data exists in a JSON format.
        if not os.path.exists(util.resource_path(f"{expansion}/card_ratings.json")):
            util.log_info(
                f"Card Ratings JSON not found for format {expansion}. Converting from CSV."
            )
            fetch.ratings_to_json(expansion)
            self.expansion = expansion
        else:
            self.expansion = expansion
            util.log_info(f"Successfully initialized card data for format {expansion}")

    def start_draft_monitor_thread(self):
        """Starts the draft monitor thread as a daemon."""
        # Thread to handle file monitoring and not block Tkinter's main loop
        self.draft_monitor_thread = threading.Thread(target=self.monitor_for_draft)
        self.draft_monitor_thread.daemon = True
        self.draft_monitor_thread.start()

    def monitor_for_draft(self):
        """Creates an event handler and an observer to register draft log creation
        in the log folder.
        """
        util.log_info("Started monitoring thread.")
        event_handler = LoggingEventHandler()
        event_handler.on_created = (
            lambda event: self.start_log_monitor_thread(event.src_path)
            if event.event_type == "created"
            and logs.is_valid_draft(event.src_path, self.log_path, self.mtgo_user)
            else util.log_info(
                f"Unrelated event at {event.src_path}: {event.event_type}"
            )
        )

        self.draft_observer = Observer()
        self.draft_observer.schedule(event_handler, path=self.log_path, recursive=True)
        self.draft_observer.start()

        try:
            while (
                self.draft_observer.is_alive()
            ):  # Keep waiting until the observer is alive.
                self.draft_observer.join(timeout=1)
        except KeyboardInterrupt:
            self.draft_observer.stop()

    def start_log_monitor_thread(self, path):
        """Gets called once a new log file has been created in the monitored location.
        Stops the event handler watching for file creating and starts the log monitor thread.
        """
        util.log_info("Draft has started.")
        self.log_file_path = path
        self.draft_observer.stop()
        self.root.after(0, self.draft_observer.join)

        self.log_monitor_thread = threading.Thread(target=self.monitor_log_file)
        self.log_monitor_thread.daemon = True
        self.log_monitor_thread.start()

    def monitor_log_file(self):
        """Ensures format data exists.
        Starts an event handler and a log observer for the MTGO draft log.
        """
        util.log_info(f"Monitoring at {self.log_file_path}")
        expansion = self.log_file_path[-7:][:3].lower()

        self.ensure_data(expansion)

        self.log = logs.Log(self.log_file_path)

        event_handler = LoggingEventHandler()
        event_handler.on_modified = (
            lambda event: self.update_labels()
            if event.event_type == "modified" and event.src_path == self.log_file_path
            else util.log_info(
                f"Unrelated event at {event.src_path}: {event.event_type}"
            )
        )

        self.log_observer = Observer()
        self.log_observer.schedule(event_handler, path=self.log_path, recursive=True)
        self.log_observer.start()

        try:
            while (
                self.log_observer.is_alive()
            ):  # Keep waiting until the observer is alive.
                self.log_observer.join(timeout=1)
        except KeyboardInterrupt:
            self.log_observer.stop()

    def update_labels(self):
        """Gets called upon modification of the monitored log file.
        Checks if a new pack or pick is present and calls the various submethods.
        """

        if len(self.log.picks) != 0:
            # Check if the log file changed in a meaningful way
            status = self.log.check_for_update()
            if status == "nothing":
                return

            if status == "picked":
                # If a new pick has been made, the pack is no longer on screen.
                util.log_info(f"New pick: {self.log.picks[-1]}")
                # Clear old labels
                self.root.after(0, self.clear_labels)
                if len(self.log.picks) >= 42:
                    self.root.after(0, self.finish_draft)
                return

        util.log_info(f"New pack: {self.log.current_pack}")

        # If there is a new pack in the log, grab a new screenshot
        time.sleep(0.3)
        self.screengrab, self.screen = cap.capture_mtgo()
        try:
            cards = rc.get_pos_and_names(
                self.expansion, self.screengrab, self.log.current_pack
            )
        except ValueError as e:
            util.log_warning(
                f"Error encountered while trying to find cards on screen: {e}"
            )
            time.sleep(0.3)
            self.screengrab, self.screen = cap.capture_mtgo()
            try:
                cards = rc.get_pos_and_names(
                    self.expansion, self.screengrab, self.log.current_pack
                )
            except ValueError as e:
                util.log_warning(
                    f"Error encountered while trying to find cards on screen: {e}"
                )
                self.root.after(0, self.update_labels)
                return

        # Try five times to find at least 30% of the cards in the pack on screen.
        # attempts = 0
        """ if len(self.log.current_pack) > 3:
            while len(cards) / len(self.log.current_pack) < 0.3:
                attempts += 1
                if attempts == 5:
                    break
                util.log_warning("Could not find enough cards on screen. Retrying.")
                self.screengrab, self.screen = cap.capture_mtgo()
                try:
                    cards = rc.get_pos_and_names(
                        self.expansion, self.screengrab, self.log.current_pack
                    )
                    if len(cards) / len(self.log.current_pack) >= 0.3:
                        break
                except ValueError as e:
                    util.log_exception(
                        f"Error encountered while trying to find cards on screen: {e}"
                    )
                    self.root.after(0, self.update_labels)
                    return """

        cards = rc.normalize_positions(cards)
        width = self.screengrab.shape[1]
        height = self.screengrab.shape[0]
        w_scale = 1920 / width
        h_scale = 1080 / height
        screen_x = self.screen.topleft[0]
        screen_y = self.screen.topleft[1]
        self.offsets = w_scale, h_scale, screen_x, screen_y
        
        ratings = fetch.get_card_ratings(self.expansion, [card for card in cards.keys()])
        for card, rating in zip(cards.items(), ratings):
            self.root.after(0, self.create_label, card, rating)


    def clear_labels(self):
        """Clear all card rating labels."""
        for label in self.labels:
            label.destroy()
        self.labels.clear()

    def create_label(self, card, rating):
        """Create card ratings labels.
        :param text_pos (tuple): a tuple (x, y) for the estimated position of the card
        :param rating (float): the 17Lands GIH WR% rating
        """
        if card[0] in ['Plains', 'Island', 'Swamp', 'Mountain', 'Forest']:
            return
        label_window = tk.Toplevel(self.root)
        label_window.overrideredirect(True)
        label_window.wm_attributes("-topmost", True)
        label_window.configure(bg="#5b5b5b")
        if not rating:
            label_text = "GIH% N/A"
        else:
            label_text = f"GIH {rating}"

        label_font = font.Font(
            family="Segoe UI",
            size=11 if "Segoe UI" in font.families() else ("Arial", 11),
        )

        text_x = card[1][0] + card[1][2] - int(18 * self.offsets[0]) + self.offsets[2]
        text_y = card[1][1] + int(20 * self.offsets[1]) + self.offsets[3]


        text_width = label_font.measure(label_text)
        text_height = label_font.metrics("linespace")
        label_window.geometry(
            f"{text_width + 10}x{text_height + 10}+{text_x - text_width}+{text_y}"
        )

        label = tk.Label(
            label_window, text=label_text, font=label_font, fg="white", bg="#707070"
        )

        label.pack(fill=tk.BOTH, expand=True)
        hwnd = label_window.winfo_id()
        make_window_click_through(hwnd)
        self.labels.append(label_window)
