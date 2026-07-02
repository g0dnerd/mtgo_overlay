"""Watch the MTGO log folder and surface changes as Qt signals.

Watchdog runs in its own thread and emits Qt signals which,
delivered to slots living on the UI thread, marshal automatically via a queued connection.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..system.logging_setup import get_logger
from .log_parser import get_current_log, is_valid_draft

_log = get_logger("log_watcher")


class _Handler(FileSystemEventHandler):
    def __init__(self, watcher: "DraftLogWatcher") -> None:
        self._watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._watcher.handle_created(str(event.src_path))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._watcher.handle_modified(str(event.src_path))


class DraftLogWatcher(QObject):
    """Emits :pyattr:`draftStarted` when a new draft log appears and
    :pyattr:`logModified` when the active log changes."""

    draftStarted = Signal(str)
    logModified = Signal(str)

    def __init__(
        self, log_dir: str | Path, username: str, parent: QObject | None = None
    ):
        super().__init__(parent)
        self.log_dir = str(log_dir)
        self.username = username
        self.active_log: str | None = None
        self._observer: Observer | None = None

    def start(self) -> None:
        self.stop()
        self._observer = Observer()
        self._observer.schedule(_Handler(self), self.log_dir, recursive=False)
        self._observer.start()
        _log.info("Watching %s for %s*.txt", self.log_dir, self.username)
        self._adopt_existing_log()

    def _adopt_existing_log(self) -> None:
        """Engage the newest pre-existing draft log, if any.

        MTGO writes a new file per draft, so a draft already in progress when the
        overlay launches fires no ``on_created`` and would otherwise be missed.
        This mirrors :meth:`handle_created` for that file; the controller decides
        whether it is still live."""
        try:
            path = get_current_log(self.log_dir, self.username)
        except (ValueError, OSError):
            return  # nothing matching in the folder
        self.active_log = path
        _log.info("Found existing draft log on startup: %s", path)
        self.draftStarted.emit(path)

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None

    def set_active_log(self, path: str | Path | None) -> None:
        self.active_log = str(path) if path else None

    # --- handler hooks (called from the watchdog thread; also unit-testable) --

    def handle_created(self, src_path: str) -> None:
        if is_valid_draft(src_path, self.log_dir, self.username):
            self.active_log = str(src_path)
            _log.info("Draft started: %s", src_path)
            self.draftStarted.emit(str(src_path))

    def handle_modified(self, src_path: str) -> None:
        if self.active_log is None:
            return
        if Path(src_path).resolve() == Path(self.active_log).resolve():
            self.logModified.emit(str(src_path))
