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

The pure helpers (:func:`map_capture_to_logical`, :func:`build_label_specs`,
:func:`expansion_from_log_path`) carry the logic that matters and are unit-tested
without Qt or Windows.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .capture.screen_capture import capture_client_area
from .config.settings import Settings
from .data import expansions, sets
from .data.expansions import SupportedSets
from .data.ratings_repo import CardRating, RatingsRepository
from .data.seventeenlands import SeventeenLandsClient
from .draft.log_parser import Log
from .draft.log_watcher import DraftLogWatcher
from .overlay.overlay_window import LabelSpec, OverlayWindow, percentile_rank
from .overlay.window_tracker import WindowTracker
from .recognition import scryfall_art
from .recognition.config import RecognitionConfig
from .recognition.pipeline import locate_cards
from .recognition.types import CardLocation
from .system import logging_setup, paths, win32

_log = logging_setup.get_logger("app")

PICKS_PER_DRAFT = 42

# Wait this long after the debounce before capturing, so MTGO has finished
# loading the new pack's card art (capturing too early yields a blank grid).
_RECOGNITION_SETTLE_MS = 75


# --- pure helpers (unit-tested) --------------------------------------------


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
    located: list[CardLocation],
    ratings: list[CardRating],
    dpr: float,
    distribution: list[float] | None = None,
) -> list[LabelSpec]:
    """Join located cards with ratings by name, mapping to overlay coords.

    Each label's color tier is the card's percentile within ``distribution`` (the
    whole set's GIH WRs), so coloring is set-relative rather than absolute. Names
    absent from ``ratings`` (basic lands are filtered upstream) get no label.
    """
    rating_by_name = {r.name: r.gih_wr for r in ratings}
    dist = distribution or []
    specs: list[LabelSpec] = []
    for loc in located:
        if loc.name not in rating_by_name:
            continue
        wr = rating_by_name[loc.name]
        tier = percentile_rank(wr, dist) if wr is not None else None
        x, y, w, h = map_capture_to_logical(loc.bbox.as_tuple(), dpr)
        specs.append(LabelSpec(wr, tier, x, y, w, h))
    return specs


def _dump_capture(screen, generation: int) -> None:
    """Persist the exact frame recognition saw (DEBUG only) so a flaky live
    detection can be replayed offline with ``tools/annotate_preview.py``."""
    try:
        import cv2

        out = paths.logs_dir() / f"capture_gen{generation}.png"
        cv2.imwrite(str(out), screen)
        _log.debug("Saved capture frame to %s", out)
    except Exception as exc:  # noqa: BLE001
        _log.debug("Could not save capture frame: %s", exc)


def expansion_from_log_path(path: str) -> str:
    """Derive the 17lands expansion code from a draft-log filename.

    Faithful to the old behavior: the 3 chars before ``.txt`` are the set code.
    """
    code = Path(path).name[-7:][:3]
    return sets.expansion_from_log_code(code)


def _set_icon(code: str, color):
    """A theme-tinted QIcon for a set's cached symbol SVG, or an empty QIcon.

    Scryfall symbols are solid black, so they're recolored to ``color`` (the
    menu's text color) via a source-in composite to stay visible on any theme.
    """
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap

    try:
        from PySide6.QtSvg import QSvgRenderer
    except ImportError:  # QtSvg not bundled -> picker falls back to text-only
        return QIcon()

    path = scryfall_art.cached_set_icon(code)
    if path is None:
        return QIcon()
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return QIcon()
    size = QSize(16, 16)
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, pixmap.rect().toRectF())
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), color)
    painter.end()
    return QIcon(pixmap)


def _pick_set(codes: list[str], parent=None) -> str | None:
    """Modal set picker: a combo of ``codes`` each prefixed by its symbol.

    Returns the chosen uppercase code, or ``None`` if cancelled / empty.
    """
    from PySide6.QtWidgets import (
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
    )

    dialog = QDialog(parent)
    dialog.setWindowTitle("Pre-download a set")
    combo = QComboBox(dialog)
    combo.setEditable(True)
    text_color = combo.palette().text().color()
    for code in codes:
        combo.addItem(_set_icon(code, text_color), code)

    buttons = QDialogButtonBox(
        QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog
    )
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)

    layout = QFormLayout(dialog)
    layout.addRow("Set:", combo)
    layout.addRow(buttons)

    if dialog.exec() != QDialog.Accepted:
        return None
    code = combo.currentText().strip().upper()
    return code or None


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
            if _log.isEnabledFor(logging.DEBUG):
                _dump_capture(screen, self.generation)
            located = locate_cards(screen, self.names, self.expansion, self.cfg)
            located_names = [loc.name for loc in located]
            missing = [n for n in self.names if n not in set(located_names)]
            _log.info(
                "Located %d/%d card(s) for %s.",
                len(located),
                len(self.names),
                self.expansion,
            )
            if missing:
                _log.info(
                    "Cards NOT located (%d): %s", len(missing), "; ".join(missing)
                )
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
        _log.info(
            "Preparing %s draft data (%d card names): ratings + Scryfall artwork.",
            self.expansion,
            len(self.names),
        )
        try:
            csv_path = (
                Path(self.settings.manual_csv_path)
                if self.settings.manual_csv_path
                else None
            )
            self.repo.ensure(
                self.expansion,
                self.fmt,
                use_live=self.settings.use_live_17lands,
                csv_path=csv_path,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("Ratings ensure failed: %s", exc)
        try:
            _log.info(
                "Warming Scryfall artwork cache for %s (cache-first; the first draft "
                "of a set downloads art at <=10 req/s and can take a while).",
                self.expansion,
            )
            scryfall_art.ensure_set_artwork(
                self.expansion, self.names, paths.scryfall_cache_dir()
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("Artwork warm failed: %s", exc)
        _log.info(
            "Draft data ready for %s — recognition can now run offline.", self.expansion
        )
        self.signals.labelsReady.emit(None)


class _PrefetchWorker(QRunnable):
    """Off-UI: enumerate a whole set on Scryfall, warm its artwork cache, and warm
    its 17lands ratings — so the next live draft of the set runs fully offline.

    Backs the tray's manual "Download set…" action (the live path only warms
    per-pack). Ratings are best-effort: a 17lands hiccup never fails the slow,
    valuable art download."""

    def __init__(self, expansion, fmt, repo, on_done):
        super().__init__()
        self.expansion = expansion
        self.fmt = fmt
        self.repo = repo
        self.signals = _WorkerSignals()
        self.signals.labelsReady.connect(on_done)

    def run(self) -> None:
        try:
            names = scryfall_art.enumerate_set_cards(self.expansion)
            if not names:
                raise RuntimeError(
                    f"Scryfall returned no cards for set '{self.expansion}'"
                )
            _log.info(
                "Downloading artwork for %d %s card name(s) from Scryfall...",
                len(names),
                self.expansion,
            )
            scryfall_art.ensure_set_artwork(
                self.expansion, names, paths.scryfall_cache_dir()
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Set artwork download failed for %s: %s", self.expansion, exc)
            self.signals.labelsReady.emit(
                {"expansion": self.expansion, "ok": False, "error": str(exc)}
            )
            return
        ratings_ok = True
        try:
            _log.info("Warming 17lands ratings for %s/%s...", self.expansion, self.fmt)
            self.repo.ensure(self.expansion, self.fmt, use_live=True, csv_path=None)
        except Exception as exc:  # noqa: BLE001 - ratings is best-effort here
            ratings_ok = False
            _log.warning(
                "Ratings warm failed for %s/%s: %s", self.expansion, self.fmt, exc
            )
        self.signals.labelsReady.emit(
            {
                "expansion": self.expansion,
                "count": len(names),
                "ok": True,
                "ratings_ok": ratings_ok,
            }
        )


class _SupportedSetsWorker(QRunnable):
    """Off-UI: load (cache-first) the 17lands supported-set list for the picker, so
    the tray menu never does network on the UI thread."""

    def __init__(self, supported, on_done):
        super().__init__()
        self.supported = supported
        self.signals = _WorkerSignals()
        self.signals.labelsReady.connect(on_done)

    def run(self) -> None:
        try:
            filters = self.supported.ensure()
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Supported-set list load failed: %s", exc)
            filters = {}
        try:
            codes = expansions.codes_newest_first(filters, mtgo_only=True)
            scryfall_art.ensure_set_icons(codes)
        except Exception as exc:  # noqa: BLE001 - icons are cosmetic
            _log.warning("Set-icon warm failed: %s", exc)
        self.signals.labelsReady.emit(filters)


# --- controller -------------------------------------------------------------


class AppController(QObject):
    def __init__(
        self, app: QApplication, settings: Settings | None = None, parent=None
    ):
        super().__init__(parent)
        self.app = app
        self.settings = settings or Settings.load()
        self.cfg = RecognitionConfig()
        client = SeventeenLandsClient(self.settings.user_agent)
        self.repo = RatingsRepository(paths.ratings_cache_dir(), client=client)
        self._supported = SupportedSets(client, paths.cache_dir())
        self._filters: dict = {}
        self.overlay = OverlayWindow(self.settings.overlay)
        self.pool = QThreadPool.globalInstance()
        self.tracker = WindowTracker(hz=10)

        self.watcher: DraftLogWatcher | None = None
        self.log: Log | None = None
        self.expansion = ""
        self._generation = 0
        self._draft_prepared = False
        # True between a pick and the next pack: the picked pack lingers in
        # Log.current_pack, so suppress recognition until a new pack lands.
        self._awaiting_pack = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._dispatch_recognition)

        self.tracker.moved.connect(self._on_moved)
        self.tracker.resized.connect(self._on_resized)
        self.tracker.lost.connect(self._on_lost)
        self.tracker.focusChanged.connect(self._on_focus_changed)

        self._tray = None
        self._mtgo_present = False
        self._shutdown_done = False

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._log_environment()
        self._setup_tray()
        _log.info("Tray icon shown.")
        self.overlay.show()
        _log.info("Overlay window shown (frameless, transparent, click-through).")
        self.tracker.start()
        _log.info("Window tracker started — polling for the MTGO window at 10 Hz.")
        self.pool.start(_SupportedSetsWorker(self._supported, self._on_supported_sets))
        self._restart_watcher()

    def _log_environment(self) -> None:
        cfg_path = paths.config_file()
        _log.info(
            "Config: %s (%s).",
            cfg_path,
            "loaded" if cfg_path.exists() else "not found — using defaults",
        )
        _log.info(
            "MTGO username: %s",
            self.settings.mtgo_username or "NOT SET — set it from the tray menu",
        )
        if self.settings.log_dir:
            _log.info(
                "Log folder: %s (exists=%s).",
                self.settings.log_dir,
                Path(self.settings.log_dir).is_dir(),
            )
        else:
            _log.info("Log folder: NOT SET — set it from the tray menu.")
        _log.info("Draft format: %s", self.settings.fmt)
        if self.settings.use_live_17lands:
            _log.info("Ratings source: live 17lands endpoint.")
        elif self.settings.manual_csv_path:
            _log.info(
                "Ratings source: CSV %s (exists=%s).",
                self.settings.manual_csv_path,
                Path(self.settings.manual_csv_path).is_file(),
            )
        else:
            _log.warning(
                "Ratings source: none configured — every label will read 'GIH N/A'. "
                "Set manual_csv_path in %s to a 17lands card_ratings.csv export, or "
                "set use_live_17lands=true.",
                cfg_path,
            )
        _log.info(
            "Caches: ratings=%s | scryfall=%s",
            paths.ratings_cache_dir(),
            paths.scryfall_cache_dir(),
        )

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
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
        self.watcher = DraftLogWatcher(
            self.settings.log_dir, self.settings.mtgo_username
        )
        self.watcher.draftStarted.connect(self._on_draft_started)
        self.watcher.logModified.connect(self._on_log_modified)
        self.watcher.start()

    # --- draft state machine -------------------------------------------------

    def _on_draft_started(self, path: str) -> None:
        self.log = Log(path)
        self.expansion = expansion_from_log_path(path)
        if len(self.log.picks) >= PICKS_PER_DRAFT:
            _log.info(
                "Log %s is a completed draft (%d picks); waiting for a new one.",
                path,
                len(self.log.picks),
            )
            self.log = None
            if self.watcher is not None:
                self.watcher.set_active_log(None)
            return
        _log.info(
            "Draft started: expansion=%s, %d picks so far",
            self.expansion,
            len(self.log.picks),
        )
        self._draft_prepared = False
        self._awaiting_pack = False
        # MTGO (and the replay tool) usually create the log file before the first
        # pack is written, so current_pack is empty here. Defer warming to the
        # first real pack instead of warming artwork for 0 names.
        if self.log.current_pack:
            self._prepare_draft_data()

    def _prepare_draft_data(self) -> None:
        """Warm ratings + artwork for the current pack, then recognize. Runs once
        per draft, as soon as a non-empty pack is known."""
        self._draft_prepared = True
        worker = _EnsureWorker(
            self.repo,
            self.expansion,
            self.settings.fmt,
            self.log.current_pack,
            self.settings,
            self._schedule_recognition,
        )
        self.pool.start(worker)

    def _on_log_modified(self, _path: str) -> None:
        if self.log is None:
            return
        status = self.log.check_for_update()
        if status == "nothing":
            return
        if status == "picked":
            self._awaiting_pack = True
            self.overlay.clear()
            if len(self.log.picks) >= PICKS_PER_DRAFT:
                self._finish_draft()
            return
        # "new" pack on screen.
        self._awaiting_pack = False
        if not self._draft_prepared:
            self._prepare_draft_data()
        else:
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
        if self.log is None or not self.log.current_pack or self._awaiting_pack:
            return
        hwnd = self.tracker.hwnd or win32.find_mtgo_hwnd()
        if hwnd is None:
            _log.warning("Cannot recognize: MTGO window not found.")
            return
        # Capturing a backgrounded window yields a black/stale frame, so skip the
        # doomed work; the refocus handler re-runs recognition when MTGO returns.
        if win32.get_foreground_hwnd() != hwnd:
            _log.info("MTGO not focused — deferring recognition until refocus.")
            return
        self._generation += 1
        gen = self._generation
        names = list(self.log.current_pack)
        _log.info(
            "Recognizing pack of %d card(s) (gen %d) for %s/%s.",
            len(names),
            gen,
            self.expansion,
            self.settings.fmt,
        )
        QTimer.singleShot(
            _RECOGNITION_SETTLE_MS,
            lambda: self._start_recognition_worker(gen, hwnd, names),
        )

    def _start_recognition_worker(
        self, generation: int, hwnd: int, names: list[str]
    ) -> None:
        worker = RecognitionWorker(
            generation,
            hwnd,
            names,
            self.expansion,
            self.settings.fmt,
            self.repo,
            self.cfg,
        )
        worker.signals.labelsReady.connect(self._on_labels)
        self.pool.start(worker)

    def _on_labels(self, payload: dict) -> None:
        if self._awaiting_pack:
            return  # a pick landed while this worker was in flight
        if payload["generation"] != self._generation:
            _log.info(
                "Dropping stale recognition result (gen %d; current %d).",
                payload["generation"],
                self._generation,
            )
            return  # superseded by a newer pack
        dpr = self._device_pixel_ratio()
        # Read fresh each pack so a re-imported CSV reshapes the percentiles.
        distribution = self.repo.distribution(self.expansion, self.settings.fmt)
        specs = build_label_specs(
            payload["located"], payload["ratings"], dpr, distribution
        )
        _log.info(
            "Recognition done: located %d card(s) -> %d label(s) shown "
            "(dpr=%.2f, set distribution n=%d).",
            len(payload["located"]),
            len(specs),
            dpr,
            len(distribution),
        )
        self.overlay.set_labels(specs)

    # --- overlay positioning -------------------------------------------------

    def _device_pixel_ratio(self) -> float:
        screen = self.overlay.screen()
        return float(screen.devicePixelRatio()) if screen else 1.0

    def _reposition(self, physical_rect: tuple[int, int, int, int]) -> None:
        dpr = self._device_pixel_ratio()
        x, y, w, h = map_capture_to_logical(physical_rect, dpr)
        self.overlay.setGeometry(x, y, w, h)

    def _note_mtgo_found(self, x, y, w, h) -> None:
        if not self._mtgo_present:
            self._mtgo_present = True
            _log.info("MTGO window found: client rect (%d,%d) %dx%d.", x, y, w, h)

    def _on_moved(self, x, y, w, h) -> None:
        self._note_mtgo_found(x, y, w, h)
        self._reposition((x, y, w, h))

    def _on_resized(self, x, y, w, h) -> None:
        self._note_mtgo_found(x, y, w, h)
        self._reposition((x, y, w, h))
        if self.log is not None:
            self._schedule_recognition()

    def _on_lost(self) -> None:
        if self._mtgo_present:
            self._mtgo_present = False
            _log.info("MTGO window lost — clearing overlay.")
        self.overlay.clear()

    def _on_focus_changed(self, focused: bool) -> None:
        # Keep the labels but hide the window when MTGO isn't the active window,
        # so the overlay never plasters win rates over other apps.
        _log.debug(
            "MTGO %s focus — overlay %s.",
            "gained" if focused else "lost",
            "shown" if focused else "hidden",
        )
        self.overlay.setVisible(focused)
        # A pick that landed while MTGO was backgrounded was deferred, not run, so
        # re-recognize on refocus to populate the now-visible overlay.
        if focused and self.log is not None and self.log.current_pack:
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
            ("Set ratings CSV", self._prompt_ratings_csv),
        ):
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)

        download_action = QAction("Download set…", menu)
        download_action.triggered.connect(self._prompt_download_set)
        menu.addAction(download_action)

        menu.addSeparator()
        clear_action = QAction("Clear local data…", menu)
        clear_action.triggered.connect(self._prompt_clear_data)
        menu.addAction(clear_action)

        menu.addSeparator()
        exit_action = QAction("Exit", menu)
        exit_action.triggered.connect(self._exit)
        menu.addAction(exit_action)

        self._tray.setContextMenu(menu)
        self._tray.show()

    def _on_supported_sets(self, filters: dict) -> None:
        self._filters = filters or {}
        _log.info(
            "Supported-set list ready (%d MTGO set(s)).",
            len(expansions.codes_newest_first(self._filters, mtgo_only=True)),
        )

    def _prompt_download_set(self) -> None:
        codes = expansions.codes_newest_first(self._filters, mtgo_only=True)
        code = _pick_set(codes)
        if code:
            self._prefetch_set(code)

    def _prefetch_set(self, expansion: str) -> None:
        fmt = expansions.format_for(expansion, self.settings.fmt, self._filters)
        _log.info(
            "Manual download requested for %s (art + %s ratings).", expansion, fmt
        )
        if self._tray is not None:
            self._tray.showMessage(
                "MTGO 17lands Overlay",
                f"Downloading {expansion} card art + ratings — first run can take a "
                f"minute or two; watch the terminal/log for progress.",
            )
        self.pool.start(
            _PrefetchWorker(expansion, fmt, self.repo, self._on_prefetch_done)
        )

    def _on_prefetch_done(self, result: dict) -> None:
        if result.get("ok"):
            ratings = (
                "ratings cached" if result.get("ratings_ok") else "ratings unavailable"
            )
            msg = (
                f"{result['expansion']} ready — {result['count']} card(s) art cached, "
                f"{ratings}."
            )
        else:
            msg = f"{result['expansion']} download failed: {result.get('error', 'see log')}"
        _log.info("%s", msg)
        if self._tray is not None:
            self._tray.showMessage("MTGO 17lands Overlay", msg)

    def _prompt_ratings_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select 17lands card_ratings CSV",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.settings.manual_csv_path = path
            self.settings.save()
            _log.info("Ratings CSV set to %s.", path)
            self._reimport_ratings()

    def _reimport_ratings(self) -> None:
        """Rebuild the ratings cache from the current CSV and re-recognize, so new
        win rates *and* their set-relative colors take effect without a new draft."""
        if self.log is None or not self.log.current_pack:
            return
        _log.info("Re-importing ratings for the active %s pack.", self.expansion)
        worker = _EnsureWorker(
            self.repo,
            self.expansion,
            self.settings.fmt,
            self.log.current_pack,
            self.settings,
            self._schedule_recognition,
        )
        self.pool.start(worker)

    def _prompt_username(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(
            None, "MTGO Username", "Enter your MTGO username:"
        )
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

    def _prompt_clear_data(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Clear local data")
        box.setText(
            "Delete all cached card art and ratings, reset settings to "
            "defaults, and remove logs and debug captures?"
        )
        box.setInformativeText(
            "Your MTGO username and log folder will be cleared and must be "
            "re-entered. This cannot be undone."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.setDefaultButton(QMessageBox.Cancel)
        if box.exec() == QMessageBox.Yes:
            self._clear_local_data()

    def _clear_local_data(self) -> None:
        self.overlay.clear()
        self.log = None
        logging_setup.close_log_file()
        try:
            removed = paths.clear_local_data()
        finally:
            logging_setup.reopen_log_file()
        _log.info(
            "Cleared local data: %s",
            ", ".join(str(p) for p in removed) or "nothing to remove",
        )
        self.settings = Settings()
        self._restart_watcher()
        if self._tray is not None:
            self._tray.showMessage(
                "MTGO 17lands Overlay",
                "Local data cleared. Re-enter your MTGO username and log folder "
                "from the tray menu.",
            )

    def _exit(self) -> None:
        self.shutdown()
        self.app.quit()


# --- entrypoint -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    logging_setup.setup()
    _log.info("=== MTGO 17lands Overlay starting ===")
    _log.info(
        "Python %s | platform=%s | pid=%d | frozen=%s",
        sys.version.split()[0],
        sys.platform,
        os.getpid(),
        getattr(sys, "frozen", False),
    )
    _log.info("Log file: %s", paths.logs_dir() / "mtgo_overlay.log")

    win32.set_dpi_awareness()  # before QApplication
    _log.info("DPI awareness set (IS_WINDOWS=%s).", win32.IS_WINDOWS)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)
    _log.info("QApplication created.")

    controller = AppController(app)
    controller.start()

    # Ctrl+C: Python can't run its SIGINT handler while blocked in app.exec(), so
    # wake the loop ~5x/s and translate the signal into a graceful shutdown.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    sigint_timer = QTimer()
    sigint_timer.setInterval(200)
    sigint_timer.timeout.connect(lambda: None)
    sigint_timer.start()

    _log.info(
        "Startup complete — entering the Qt event loop. From here this terminal "
        "will look idle; that is normal. The app lives in the system tray: use its "
        "menu to configure, then start a draft. Quit via the tray's Exit (or Ctrl+C)."
    )
    rc = app.exec()
    controller.shutdown()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
