import time
import glob
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
# from overlay.display import update_ratings

class LogFileHandler(FileSystemEventHandler):
    def __init__(self, filename, callback):
        self.filename = filename
        self.callback = callback

    def on_modified(self, event):
        if event.src_path == self.filename:
            print(f'{self.filename} has been modified.')
            self.callback()

def monitor_log_file(path: str):
    event_handler = LogFileHandler(path, get_current_log)
    observer = Observer()
    observer.schedule(event_handler, path=path, recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

def get_current_log(mtgo_user: str):
    base_path = os.path.expanduser('~')
    logs_path = base_path + '/Documents\\'
    all_logs = glob.glob(logs_path + f'{mtgo_user}*.txt')
    newest_log = max(all_logs, key=os.path.getctime)
    return logs_path, newest_log

def get_newest_pack(path_to_log: str):
    """Parses an MTGO log file and outputs the card names from the newest pack in a list.
    Raises a ValueError if no pack was found.
    :param path_to_log (str): The full file path to the current MTGO log.
    :return (list): A list of strings of card names
    """
    
    with open(path_to_log, 'r') as log:
        lines = log.readlines()
        log.close()

    pack_found = False
    pick_found = False
    packs = []
    pack = []
    picks = []
    for line in lines:
        if pick_found:
            line = line.replace('Picked:', '')
            picks.append(line.strip())
            pick_found = False
        if pack_found:
            if line == '\n':
                pick_found = True
                pack_found = False
                packs.append(pack)
                pack = []
            else:
                line = line.replace('-->', '')
                pack.append(line.strip())
            
        if 'Pack' in line and 'pick' in line:
            pack_found = True

    if packs:
        return packs.pop()
    else:
        raise ValueError('Unable to parse draft log for pack.')
