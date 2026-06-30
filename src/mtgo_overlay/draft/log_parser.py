"""Parse MTGO draft logs into pack / pick state.

This is a faithful port of the old ``crawler/logs.py`` — the parsing logic
(``Log.check_for_update`` / ``Log.get_entry_point``) is intentionally unchanged,
since the draft log remains the authoritative source of card names. Only the path
helpers were cleaned up to ``pathlib`` so they work cross-platform for testing.
"""

from __future__ import annotations

from pathlib import Path

from ..system.logging_setup import log_warning

# Number of header lines (event / time / players) before the first pack block.
_HEADER_LINES = 12


class Log:
    """An evolving MTGO draft log file."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.picks: list[str] = []
        self.current_pack: list[str] = []
        self.cutoff_idx = _HEADER_LINES  # points at the newest unconsumed pack
        self.get_entry_point()

    def check_for_update(self) -> str:
        """Re-scan the log, advancing state. Returns ``"picked"``, ``"new"`` or
        ``"nothing"`` depending on what changed since the last call."""
        with open(self.path, "r", encoding="utf-8") as log:
            lines = log.readlines()

        if self.cutoff_idx == len(lines):
            log_warning("End of log file reached.")
            return "nothing"
        # Trim event/time/player header; start parsing from the last pick.
        lines = lines[self.cutoff_idx:]

        pack_found = False
        pack: list[str] = []
        pick = ""
        new_cutoff_idx = self.cutoff_idx  # defensive; only used on the "picked" path

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
                self.picks.append(pick)
                self.current_pack = pack
                self.cutoff_idx = new_cutoff_idx
                return "picked"
            if pack != self.current_pack:
                self.current_pack = pack
                return "new"
        return "nothing"

    def get_entry_point(self) -> None:
        """Fast-forward through everything already logged to the current pack."""
        test = "x"
        while test != "nothing":
            test = self.check_for_update()
            if test == "nothing":
                break


def get_current_log(base_path: str | Path, mtgo_user: str) -> str:
    """Newest ``<user>*.txt`` in ``base_path`` (newest by creation time)."""
    base = Path(base_path)
    candidates = list(base.glob(f"{mtgo_user}*.txt"))
    if not candidates:
        raise ValueError(f"No draft log for user {mtgo_user!r} in {base}")
    newest = max(candidates, key=lambda p: p.stat().st_ctime)
    return str(newest)


def is_valid_draft(event_path: str | Path, log_dir: str | Path, user: str) -> bool:
    """True if ``event_path`` is a draft log for ``user`` in the watched folder."""
    event = Path(event_path)
    if event.parent != Path(log_dir):
        return False
    return user in event.name
