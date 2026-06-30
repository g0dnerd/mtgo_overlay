"""Application bootstrap + the AppController state machine.

Wiring (per the plan's data flow)::

    DraftLogWatcher (watchdog thread)
        --draftStarted--> ensure ratings + warm art (pool) --> recognize
        --logModified---> Log.check_for_update -> new? recognize / picked? clear
    RecognitionWorker (QThreadPool, off-UI)
        capture client area -> locate_cards -> ratings.lookup
        --labelsReady(payload)--> AppController maps capture-px -> logical
                                  -> OverlayWindow.set_labels
    WindowTracker (UI QTimer ~10 Hz)
        --moved--> reposition overlay   --resized--> reposition + recognize

The pure helpers (:func:`format_label`, :func:`map_capture_to_logical`,
:func:`build_label_specs`, :func:`expansion_from_log_path`) carry the logic that
matters and are unit-tested without Qt or Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .capture.screen_capture import capture_client_area
from .config.settings import Settings
from .data import sets
from .data.ratings_repo import CardRating, RatingsRepository
from .data.seventeenlands import SeventeenLandsClient
from .draft.log_parser import Log
from .draft.log_watcher import DraftLogWatcher
from .overlay.overlay_window import LabelSpec, OverlayWindow
from .overlay.window_tracker import WindowTracker
from .recognition import scryfall_art
from .recognition.config import RecognitionConfig
from .recognition.pipeline import locate_cards
from .recognition.types import CardLocation
from .system import logging_setup, paths, win32

_log = logging_setup.get_logger("app")

PICKS_PER_DRAFT = 42


# --- pure helpers (unit-tested) --------------------------------------------

def format_label(gih_wr: float | None) -> str:
    return "GIH N/A" if gih_wr is None else f"GIH {gih_wr}"


def map_capture_to_logical(
    bbox: tuple[int, int, int, int], dpr: float
) -> tuple[int, int, int, int]:
    """Map a physical-pixel card box to overlay-logical coords.

    The overlay is pinned to MTGO's client origin, so this is a pure scale by the
    device pixel ratio — the last 1920x1080 assumption is gone.
    """
    x, y, w, h = bbox
    if not dpr or dpr == 1.0:
        return (x, y, w, h)
    return (round(x / dpr), round(y / dpr), round(w / dpr), round(h / dpr))


def build_label_specs(
    located: list[CardLocation], ratings: list[CardRating], dpr: float
) -> list[LabelSpec]:
    """Join located cards with ratings by name, mapping to overlay coords.

    Names absent from ``ratings`` (basic lands are filtered upstream) get no
    label, so the overlay stays dumb.
    """
    rating_by_name = {r.name: r.gih_wr for r in ratings}
    specs: list[LabelSpec] = []
    for loc in located:
        if loc.name not in rating_by_name:
            continue
        x, y, w, h = map_capture_to_logical(loc.bbox.as_tuple(), dpr)
        specs.append(LabelSpec(format_label(rating_by_name[loc.name]), x, y, w, h))
    return specs


def expansion_from_log_path(path: str) -> str:
    """Derive the 17lands expansion code from a draft-log filename.

    Faithful to the old behavior: the 3 chars before ``.txt`` are the set code.
    """
    code = Path(path).name[-7:][:3]
    return sets.expansion_from_log_code(code)


# --- recognition worker -----------------------------------------------------

class _WorkerSignals(QObject):
    labelsReady = Signal(object)
    failed = Signal(str)


class RecognitionWorker(QRunnable):
    """Off-UI: capture -> locate -> lookup, then emit a payload."""

    def __init__(self, generation, hwnd, names, expansion, fmt, repo, cfg):
        super().__init__()
        self.signals = _WorkerSignals()
        self.generation = generation
        self.hwnd = hwnd
        self.names = names
        self.expansion = expansion
        self.fmt = fmt
        self.repo = repo
        self.cfg = cfg

    def run(self) -> None:
        try:
            screen, rect = capture_client_area(self.hwnd)
            located = locate_cards(screen, self.names, self.expansion, self.cfg)
            ratings = self.repo.lookup(self.expansion, self.fmt, self.names)
            self.signals.labelsReady.emit(
                {
                    "generation": self.generation,
                    "located": located,
                    "ratings": ratings,
                    "rect": rect,
                }
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Recognition failed: %s", exc)
            self.signals.failed.emit(str(exc))


class _EnsureWorker(QRunnable):
    """Off-UI: warm the ratings cache + (owner's) artwork cache for a draft."""

    def __init__(self, repo, expansion, fmt, names, settings, on_done):
        super().__init__()
        self.repo = repo
        self.expansion = expansion
        self.fmt = fmt
        self.names = names
        self.settings = settings
        self._on_done = on_done
        self.signals = _WorkerSignals()
        self.signals.labelsReady.connect(lambda _p: on_done())

    def run(self) -> None:
        try:
            csv_path = Path(self.settings.manual_csv_path) if self.settings.manual_csv_path else None
            self.repo.ensure(
                self.expansion,
                self.fmt,
                use_live=self.settings.use_live_17lands,
                csv_path=csv_path,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("Ratings ensure failed: %s", exc)
        try:
            scryfall_art.ensure_set_artwork(
                self.expansion, self.names, paths.scryfall_cache_dir()
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("Artwork warm failed: %s", exc)
        self.signals.labelsReady.emit(None)


# --- controller -------------------------------------------------------------

class AppController(QObject):
    def __init__(self, app: QApplication, settings: Settings | None = None, parent=None):
        super().__init__(parent)
        self.app = app
        self.settings = settings or Settings.load()
        self.cfg = RecognitionConfig()
        client = SeventeenLandsClient(self.settings.user_agent)
        self.repo = RatingsRepository(paths.ratings_cache_dir(), client=client)
        self.overlay = OverlayWindow(self.settings.overlay)
        self.pool = QThreadPool.globalInstance()
        self.tracker = WindowTracker(hz=10)

        self.watcher: DraftLogWatcher | None = None
        self.log: Log | None = None
        self.expansion = ""
        self._generation = 0

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._dispatch_recognition)

        self.tracker.moved.connect(self._on_moved)
        self.tracker.resized.connect(self._on_resized)
        self.tracker.lost.connect(self.overlay.clear)

        self._tray = None

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._setup_tray()
        self.overlay.show()
        self.tracker.start()
        self._restart_watcher()

    def shutdown(self) -> None:
        self._debounce.stop()
        self.tracker.stop()
        if self.watcher is not None:
            self.watcher.stop()
        self.pool.waitForDone(3000)
        self.overlay.close()
        if self._tray is not None:
            self._tray.hide()

    def _restart_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None
        if not (self.settings.mtgo_username and self.settings.log_dir):
            _log.info("Username / log folder not set; configure via the tray menu.")
            return
        self.watcher = DraftLogWatcher(self.settings.log_dir, self.settings.mtgo_username)
        self.watcher.draftStarted.connect(self._on_draft_started)
        self.watcher.logModified.connect(self._on_log_modified)
        self.watcher.start()

    # --- draft state machine -------------------------------------------------

    def _on_draft_started(self, path: str) -> None:
        self.log = Log(path)
        self.expansion = expansion_from_log_path(path)
        _log.info("Draft started: expansion=%s, %d picks so far",
                  self.expansion, len(self.log.picks))
        worker = _EnsureWorker(
            self.repo, self.expansion, self.settings.fmt,
            self.log.current_pack, self.settings, self._schedule_recognition
        )
        self.pool.start(worker)

    def _on_log_modified(self, _path: str) -> None:
        if self.log is None:
            return
        status = self.log.check_for_update()
        if status == "nothing":
            return
        if status == "picked":
            self.overlay.clear()
            if len(self.log.picks) >= PICKS_PER_DRAFT:
                self._finish_draft()
            return
        # "new" pack on screen.
        self._schedule_recognition()

    def _finish_draft(self) -> None:
        _log.info("Draft finished. Waiting for the next one.")
        self.log = None
        if self.watcher is not None:
            self.watcher.set_active_log(None)
        self.overlay.clear()

    # --- recognition ---------------------------------------------------------

    def _schedule_recognition(self) -> None:
        self._debounce.start()

    def _dispatch_recognition(self) -> None:
        if self.log is None or not self.log.current_pack:
            return
        hwnd = self.tracker.hwnd or win32.find_mtgo_hwnd()
        if hwnd is None:
            _log.warning("Cannot recognize: MTGO window not found.")
            return
        self._generation += 1
        worker = RecognitionWorker(
            self._generation, hwnd, list(self.log.current_pack),
            self.expansion, self.settings.fmt, self.repo, self.cfg,
        )
        worker.signals.labelsReady.connect(self._on_labels)
        self.pool.start(worker)

    def _on_labels(self, payload: dict) -> None:
        if payload["generation"] != self._generation:
            return  # superseded by a newer pack
        dpr = self._device_pixel_ratio()
        specs = build_label_specs(payload["located"], payload["ratings"], dpr)
        self.overlay.set_labels(specs)

    # --- overlay positioning -------------------------------------------------

    def _device_pixel_ratio(self) -> float:
        screen = self.overlay.screen()
        return float(screen.devicePixelRatio()) if screen else 1.0

    def _reposition(self, physical_rect: tuple[int, int, int, int]) -> None:
        dpr = self._device_pixel_ratio()
        x, y, w, h = map_capture_to_logical(physical_rect, dpr)
        self.overlay.setGeometry(x, y, w, h)

    def _on_moved(self, x, y, w, h) -> None:
        self._reposition((x, y, w, h))

    def _on_resized(self, x, y, w, h) -> None:
        self._reposition((x, y, w, h))
        if self.log is not None:
            self._schedule_recognition()

    # --- tray ----------------------------------------------------------------

    def _setup_tray(self) -> None:
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        from .system.resources import resource_path

        icon_path = resource_path("assets/tray.ico")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._tray = QSystemTrayIcon(icon, self.app)
        self._tray.setToolTip("MTGO 17lands Overlay")

        menu = QMenu()
        for text, slot in (
            ("Reboot", self._restart_watcher),
            ("Enter MTGO username", self._prompt_username),
            ("Change log folder", self._prompt_log_folder),
            ("Exit", self._exit),
        ):
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)
        self._tray.setContextMenu(menu)
        self._tray.show()

    def _prompt_username(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(None, "MTGO Username", "Enter your MTGO username:")
        if ok and name:
            self.settings.mtgo_username = name
            self.settings.save()
            self._restart_watcher()

    def _prompt_log_folder(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        folder = QFileDialog.getExistingDirectory(None, "Select MTGO log folder")
        if folder:
            self.settings.log_dir = folder
            self.settings.save()
            self._restart_watcher()

    def _exit(self) -> None:
        self.shutdown()
        self.app.quit()


# --- entrypoint -------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging_setup.setup()
    win32.set_dpi_awareness()  # before QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)

    controller = AppController(app)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
