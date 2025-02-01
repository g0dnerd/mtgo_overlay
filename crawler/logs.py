"""Holds the Log class that represents an evolving log file and some utility methods."""

import glob
import os
from data.resources import log_warning


class Log:
    """Represents an MTGO log file."""

    def __init__(self, path):
        self.path = path
        self.picks = []
        self.current_pack = []
        self.cutoff_idx = 12  # points to the location of the newest pack
        self.get_entry_point()

    def check_for_update(self):
        """ "Runs through the log to check if a new pack or a new pick have been added."""
        with open(self.path, "r", encoding="utf-8") as log:
            lines = log.readlines()
            log.close()

        if self.cutoff_idx == len(lines):
            log_warning("End of log file reached.")
            return "nothing"
        # Trim event, time and player information, start parsing from the last pick
        lines = lines[self.cutoff_idx :]

        pack_found = False
        pack = []
        pick = ""

        for idx, line in enumerate(lines):
            if "Picked: " in line:
                new_cutoff_idx = self.cutoff_idx + idx + 1
                break
            if line == "\n":
                if pack_found:
                    pack_found = False
                continue
            if "Pack" in line and "pick" in line:
                pack_found = True
                continue
            line = line.strip()
            if pack_found:  
                card = line.replace("--> ", "")
                pack.append(card)
                if "-->" in line:
                    pick = card
                if "Picked: " in line:
                    pack_found = False

        if pack:
            if pick:
                # if (
                #     not self.picks
                #     or self.picks[-1] != pick
                #     or (self.picks[-1] == pick and pack != self.current_pack)
                # ):
                self.picks.append(pick)
                self.current_pack = pack
                self.cutoff_idx = new_cutoff_idx
                return "picked"
            if pack != self.current_pack:
                self.current_pack = pack
                return "new"
        return "nothing"

    def get_entry_point(self):
        """Finds the location of the newest pack"""
        test = "x"
        while test != "nothing":
            test = self.check_for_update()
            if test == "nothing":
                break


def get_current_log(base_path: str, mtgo_user: str):
    """Gets the newest .txt file that contains the user's MTGO name in the specificed location."""
    base_path = base_path.replace("/", "\\")
    base_path += "\\"
    all_logs = glob.glob(base_path + f"{mtgo_user}*.txt")
    newest_log = max(all_logs, key=os.path.getctime)
    return newest_log


def is_valid_draft(event_path, log_path, user):
    """Checks if the specified file conforms to MTGO's log naming conventions."""
    if not os.path.dirname(event_path) == os.path.dirname(log_path):
        return False

    if user in event_path:
        return True
    return False
