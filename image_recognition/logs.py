import glob
import os

def get_current_log(base_path: str, mtgo_user: str):
    base_path = base_path.replace('/', '\\')
    base_path += '\\'
    all_logs = glob.glob(base_path + f'{mtgo_user}*.txt')
    newest_log = max(all_logs, key=os.path.getctime)
    return base_path, newest_log

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

def is_valid_draft(event_path, log_path, user):
    if not os.path.dirname(event_path) == os.path.dirname(log_path):
        return False
    
    if user in event_path:
        return True
    return False