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
import tempfile
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QApplication

from .capture.screen_capture import capture_client_area
from .config.settings import Settings
from .data import embargo, expansions, sets
from .data.expansions import SupportedSets
from .data.prices_repo import CardPrice, PricesRepository
from .data.ratings_repo import (
    GROUP_ALL,
    GROUP_TOP,
    CardRating,
    RatingsRepository,
)
from .data.seventeenlands import SeventeenLandsClient
from .draft.log_parser import Log
from .draft.log_watcher import DraftLogWatcher
from .onboarding.wizard import needs_onboarding, run_onboarding
from .overlay.overlay_window import LabelSpec, OverlayWindow, percentile_rank
from .overlay.window_tracker import WindowTracker
from .recognition import scryfall_art
from .recognition.config import RecognitionConfig
from .recognition.pipeline import locate_cards
from .recognition.types import CardLocation
from .system import logging_setup, paths, updater, win32
from . import __version__

_log = logging_setup.get_logger("app")

PICKS_PER_DRAFT = 42

# Both player cohorts are warmed per draft so the tray toggle flips between them
# with no network round-trip (2 small requests/set/day instead of 1).
RATING_GROUPS = (GROUP_ALL, GROUP_TOP)

# Wait this long after the debounce before capturing, so MTGO has finished
# loading the new pack's card art (capturing too early yields a blank grid).
_RECOGNITION_SETTLE_MS = 75


# --- pure helpers (unit-tested) --------------------------------------------


def map_capture_to_logical(
    bbox: tuple[int, int, int, int], dpr: float
) -> tuple[int, int, int, int]:
    """Map a physical-pixel card box to overlay-logical coords.

    The overlay is pinned to MTGO's client origin, so this is a pure scale by the
    device pixel ratio - the last 1920x1080 assumption is gone.
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
    prices: list[CardPrice] | None = None,
    *,
    show_prices: bool = False,
    price_min_tix: float = 0.0,
) -> list[LabelSpec]:
    """Join located cards with ratings (by name) and prices (by printing id),
    mapping to overlay coords.

    Each label's color tier is the card's percentile within ``distribution`` (the
    whole set's GIH WRs), so coloring is set-relative rather than absolute. Names
    absent from ``ratings`` (basic lands are filtered upstream) get no label. A
    price rides along only when ``show_prices`` is on and the matched printing's
    ``tix`` is at/above ``price_min_tix``.
    """
    rating_by_name = {r.name: r.gih_wr for r in ratings}
    dist = distribution or []
    tix_by_id = {p.printing_id: p.tix for p in (prices or [])}
    specs: list[LabelSpec] = []
    for loc in located:
        if loc.name not in rating_by_name:
            continue
        wr = rating_by_name[loc.name]
        tier = percentile_rank(wr, dist) if wr is not None else None
        x, y, w, h = map_capture_to_logical(loc.bbox.as_tuple(), dpr)
        tix = None
        if show_prices and loc.printing_id is not None:
            raw = tix_by_id.get(loc.printing_id)
            if raw is not None and raw >= price_min_tix:
                tix = raw
        specs.append(LabelSpec(wr, tier, x, y, w, h, tix))
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
    """Derive the 17Lands expansion code from a draft-log filename.

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

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dialog)
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

    def __init__(
        self, generation, hwnd, names, expansion, fmt, repo, price_repo, cfg, group
    ):
        super().__init__()
        self.signals = _WorkerSignals()
        self.generation = generation
        self.hwnd = hwnd
        self.names = names
        self.expansion = expansion
        self.fmt = fmt
        self.repo = repo
        self.price_repo = price_repo
        self.cfg = cfg
        self.group = group

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
            ratings = self.repo.lookup(self.expansion, self.fmt, self.names, self.group)
            printing_ids = [
                loc.printing_id for loc in located if loc.printing_id is not None
            ]
            prices = self.price_repo.lookup(self.expansion, printing_ids)
            self.signals.labelsReady.emit(
                {
                    "generation": self.generation,
                    "located": located,
                    "ratings": ratings,
                    "prices": prices,
                    "rect": rect,
                }
            )
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Recognition failed: %s", exc)
            self.signals.failed.emit(str(exc))


class _EnsureWorker(QRunnable):
    """Off-UI: warm the ratings cache + (owner's) artwork cache for a draft."""

    def __init__(
        self,
        repo,
        price_repo,
        expansion,
        fmt,
        names,
        settings,
        on_done,
        start_date=None,
        use_live=False,
    ):
        super().__init__()
        self.repo = repo
        self.price_repo = price_repo
        self.expansion = expansion
        self.fmt = fmt
        self.names = names
        self.settings = settings
        self.start_date = start_date
        self.use_live = use_live
        self._on_done = on_done
        self.signals = _WorkerSignals()
        self.signals.labelsReady.connect(lambda _p: on_done())

    def run(self) -> None:
        _log.info(
            "Preparing %s draft data (%d card names): ratings + Scryfall artwork.",
            self.expansion,
            len(self.names),
        )
        csv_path = (
            Path(self.settings.manual_csv_path)
            if self.settings.manual_csv_path
            else None
        )
        for group in RATING_GROUPS:
            try:
                self.repo.ensure(
                    self.expansion,
                    self.fmt,
                    use_live=self.use_live,
                    group=group,
                    csv_path=csv_path,
                    start_date=self.start_date,
                )
            except Exception as exc:  # noqa: BLE001
                _log.warning("Ratings ensure failed (%s): %s", group, exc)
        try:
            _log.info(
                "Warming Scryfall artwork cache for %s (cache-first; the first draft "
                "of a set downloads all of that set's artwork and can take a while).",
                self.expansion,
            )
            scryfall_art.ensure_set_artwork(
                self.expansion, self.names, paths.scryfall_cache_dir()
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("Artwork warm failed: %s", exc)
        if self.settings.show_prices:
            try:
                self.price_repo.ensure(self.expansion)
            except Exception as exc:  # noqa: BLE001
                _log.warning("Price warm failed: %s", exc)
        _log.info(
            "Draft data ready for %s - recognition can now run offline.", self.expansion
        )
        self.signals.labelsReady.emit(None)


class _PrefetchWorker(QRunnable):
    """Off-UI: enumerate a whole set on Scryfall, warm its artwork cache, and warm
    its 17Lands ratings - so the next live draft of the set runs fully offline.

    Backs the tray's manual "Download set…" action (the live path only warms
    per-pack). Ratings are best-effort: a 17Lands hiccup never fails the slow,
    valuable art download."""

    def __init__(
        self, expansion, fmt, repo, price_repo, on_done, start_date=None, use_live=True
    ):
        super().__init__()
        self.expansion = expansion
        self.fmt = fmt
        self.repo = repo
        self.price_repo = price_repo
        self.start_date = start_date
        self.use_live = use_live
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
            # Prices come from the same set data; warm them so a later draft (with
            # prices on) is fully offline regardless of the current toggle.
            try:
                self.price_repo.ensure(self.expansion)
            except Exception as exc:  # noqa: BLE001 - prices are best-effort
                _log.warning("Price warm failed for %s: %s", self.expansion, exc)
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Set artwork download failed for %s: %s", self.expansion, exc)
            self.signals.labelsReady.emit(
                {"expansion": self.expansion, "ok": False, "error": str(exc)}
            )
            return
        ratings_ok = True
        for group in RATING_GROUPS:
            if not self.use_live:
                ratings_ok = False
                break
            try:
                _log.info(
                    "Loading 17Lands ratings for %s/%s/%s...",
                    self.expansion,
                    self.fmt,
                    group,
                )
                self.repo.ensure(
                    self.expansion,
                    self.fmt,
                    use_live=True,
                    group=group,
                    csv_path=None,
                    start_date=self.start_date,
                )
            except Exception as exc:  # noqa: BLE001 - ratings is best-effort here
                ratings_ok = False
                _log.warning(
                    "Failed to load ratings for %s/%s/%s: %s",
                    self.expansion,
                    self.fmt,
                    group,
                    exc,
                )
        self.signals.labelsReady.emit(
            {
                "expansion": self.expansion,
                "count": len(names),
                "ok": True,
                "ratings_ok": ratings_ok,
                "embargoed": not self.use_live,
            }
        )


class _SupportedSetsWorker(QRunnable):
    """Off-UI: load (cache-first) the 17Lands supported-set list for the picker, so
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


class _UpdateCheckWorker(QRunnable):
    """Off-UI: ask GitHub for the latest release, emit a result dict."""

    def __init__(self, on_done):
        super().__init__()
        self.signals = _WorkerSignals()
        self.signals.labelsReady.connect(on_done)

    def run(self) -> None:
        try:
            info = updater.fetch_latest_release()
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Update check failed: %s", exc)
            self.signals.labelsReady.emit({"ok": False, "error": str(exc)})
            return
        self.signals.labelsReady.emit({"ok": True, "info": info})


class _UpdateDownloadWorker(QRunnable):
    """Off-UI: download the installer for a release, emit its local path."""

    def __init__(self, info, on_done):
        super().__init__()
        self.info = info
        self.signals = _WorkerSignals()
        self.signals.labelsReady.connect(on_done)

    def run(self) -> None:
        try:
            fd, dest = tempfile.mkstemp(suffix="-" + self.info.asset_name)
            os.close(fd)
            _log.info("Downloading update %s to %s", self.info.tag, dest)
            updater.download_installer(self.info.download_url, dest)
        except Exception as exc:  # noqa: BLE001 - worker boundary
            _log.warning("Update download failed: %s", exc)
            self.signals.labelsReady.emit(
                {"ok": False, "error": str(exc), "info": self.info}
            )
            return
        self.signals.labelsReady.emit({"ok": True, "path": dest, "info": self.info})


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
        self.price_repo = PricesRepository(paths.prices_cache_dir())
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
        # True when the active set's live data is under the 17Lands embargo and no
        # CSV fallback exists: recognition is suppressed in favor of a notice.
        self._embargo_block = False

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._dispatch_recognition)

        self.tracker.moved.connect(self._on_moved)
        self.tracker.resized.connect(self._on_resized)
        self.tracker.lost.connect(self._on_lost)
        self.tracker.focusChanged.connect(self._on_focus_changed)

        self._tray = None
        self._group_actions = None
        self._mtgo_present = False
        self._shutdown_done = False
        self._update_quiet = False

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._log_environment()
        # Run the first-run wizard before the tray so the menu (e.g. the CSV
        # item, which keys on use_live_17lands) reflects any consent given here.
        self._maybe_run_onboarding()
        self._setup_tray()
        _log.info("Tray icon shown.")
        self.overlay.show()
        _log.info("Overlay window shown.")
        self.tracker.start()
        _log.info("Window tracker started - polling for the MTGO window at 10 Hz.")
        self.pool.start(_SupportedSetsWorker(self._supported, self._on_supported_sets))
        self._restart_watcher()
        self._maybe_show_setup_toast()
        self._check_for_updates(quiet=True)

    def _maybe_run_onboarding(self) -> None:
        if not needs_onboarding(self.settings):
            return
        _log.info("First run / disclaimer not accepted - showing onboarding wizard.")
        if run_onboarding(self.settings):
            _log.info("Onboarding completed.")
        else:
            _log.info(
                "Onboarding dismissed before finishing; the tray 'Setup status…' "
                "item can resume it later."
            )

    def _missing_setup(self) -> list[str]:
        """Human-readable list of still-unconfigured essentials (empty = ready)."""
        missing: list[str] = []
        if not self.settings.mtgo_username:
            missing.append("MTGO username")
        if not self.settings.log_dir:
            missing.append("log folder")
        if not self.settings.use_live_17lands and not self.settings.manual_csv_path:
            missing.append("ratings source")
        return missing

    def _maybe_show_setup_toast(self) -> None:
        if self._tray is None:
            return
        missing = self._missing_setup()
        if missing:
            self._tray.showMessage(
                "MTGO Draft Helper - setup needed",
                "Still to configure: "
                + ", ".join(missing)
                + ". Open the tray menu → 'Setup status…' to finish.",
            )

    def _log_environment(self) -> None:
        cfg_path = paths.config_file()
        _log.info(
            "Config: %s (%s).",
            cfg_path,
            "loaded" if cfg_path.exists() else "not found - using defaults",
        )
        _log.info(
            "MTGO username: %s",
            self.settings.mtgo_username or "NOT SET - set it from the tray menu",
        )
        if self.settings.log_dir:
            _log.info(
                "Log folder: %s (exists=%s).",
                self.settings.log_dir,
                Path(self.settings.log_dir).is_dir(),
            )
        else:
            _log.info("Log folder: NOT SET - set it from the tray menu.")
        _log.info("Draft format: %s", self.settings.fmt)
        if self.settings.use_live_17lands:
            _log.info("Ratings source: live 17Lands endpoint.")
        elif self.settings.manual_csv_path:
            _log.info(
                "Ratings source: CSV %s (exists=%s).",
                self.settings.manual_csv_path,
                Path(self.settings.manual_csv_path).is_file(),
            )
        else:
            _log.warning(
                "Ratings source: none configured - every label will read 'N/A'. "
                "Set manual_csv_path in %s to a 17Lands card_ratings.csv export, or "
                "set use_live_17lands=true in config.toml.",
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
        self._embargo_block = False
        self.overlay.set_notice(None)
        # MTGO (and the replay tool) usually create the log file before the first
        # pack is written, so current_pack is empty here. Defer warming to the
        # first real pack instead of warming artwork for 0 names.
        if self.log.current_pack:
            self._prepare_draft_data()

    def _start_date_for(self, expansion: str) -> str | None:
        """The set's 17Lands start date (``YYYY-MM-DD``) for a lifetime-spanning
        live fetch, or ``None`` if the supported-set list isn't loaded yet."""
        raw = self._filters.get("start_dates", {}).get(expansion.upper())
        return raw[:10] if isinstance(raw, str) and raw else None

    def _live_blocked(self, expansion: str) -> bool:
        """Whether the live 17Lands fetch must be withheld for this set - either
        because live mode is off, or because the set is still under 17Lands'
        new-set embargo (fail-closed when the release date is unknown)."""
        if not self.settings.use_live_17lands:
            return False
        return not embargo.live_data_allowed(
            self._start_date_for(expansion), date.today()
        )

    def _ratings_from_csv(self) -> bool:
        """Whether the effective win-rate source is the user's own CSV rather than
        the live 17Lands endpoint - either because live mode is off, or the active
        set's live data is still under the new-set embargo. The top/all cohort
        split is a live-endpoint parameter, so a CSV source makes that pick moot."""
        if not self.settings.use_live_17lands:
            return True
        return bool(self.expansion) and self._live_blocked(self.expansion)

    def _embargo_notice(self, expansion: str) -> str:
        lift = embargo.lift_date(self._start_date_for(expansion))
        when = f"{lift.strftime('%b')} {lift.day}, {lift.year}" if lift else None
        return (
            f"17Lands data for {expansion} available {when}"
            if when
            else f"17Lands data for {expansion} not yet available"
        )

    def _prepare_draft_data(self) -> None:
        """Warm ratings + artwork for the current pack, then recognize. Runs once
        per draft, as soon as a non-empty pack is known."""
        self._draft_prepared = True
        blocked = self._live_blocked(self.expansion)
        # Now that the active set (and its embargo status) is known, resync the
        # tray so the cohort picks reflect whether this draft reads live or CSV.
        self._refresh_tray()
        # Embargoed with no CSV fallback: nothing to show, so suppress pills and
        # surface a notice (which doubles as the 17Lands citation) instead.
        self._embargo_block = blocked and not self.settings.manual_csv_path
        if self._embargo_block:
            _log.info(
                "Live 17Lands data for %s is under the new-set embargo and no CSV "
                "is set; showing a notice instead of pills.",
                self.expansion,
            )
            self.overlay.clear()
            self.overlay.set_notice(self._embargo_notice(self.expansion))
            return
        self.overlay.set_notice(None)
        worker = _EnsureWorker(
            self.repo,
            self.price_repo,
            self.expansion,
            self.settings.fmt,
            self.log.current_pack,
            self.settings,
            self._schedule_recognition,
            start_date=self._start_date_for(self.expansion),
            use_live=self.settings.use_live_17lands and not blocked,
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
        self._embargo_block = False
        if self.watcher is not None:
            self.watcher.set_active_log(None)
        self.overlay.clear()
        self.overlay.set_notice(None)

    # --- recognition ---------------------------------------------------------

    def _schedule_recognition(self) -> None:
        self._debounce.start()

    def _dispatch_recognition(self) -> None:
        if self.log is None or not self.log.current_pack or self._awaiting_pack:
            return
        if self._embargo_block:
            return  # embargoed set with no CSV: the notice stands in for pills
        hwnd = self.tracker.hwnd or win32.find_mtgo_hwnd()
        if hwnd is None:
            _log.warning("Cannot recognize: MTGO window not found.")
            return
        # Capturing a backgrounded window yields a black/stale frame, so skip the
        # doomed work; the refocus handler re-runs recognition when MTGO returns.
        if win32.get_foreground_hwnd() != hwnd:
            _log.info("MTGO not focused - deferring recognition until refocus.")
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
            self.price_repo,
            self.cfg,
            self.settings.user_group,
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
        distribution = self.repo.distribution(
            self.expansion, self.settings.fmt, self.settings.user_group
        )
        specs = build_label_specs(
            payload["located"],
            payload["ratings"],
            dpr,
            distribution,
            payload.get("prices"),
            show_prices=self.settings.show_prices,
            price_min_tix=self.settings.price_min_tix,
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
            _log.info("MTGO window lost - clearing overlay.")
        self.overlay.clear()

    def _on_focus_changed(self, focused: bool) -> None:
        # Keep the labels but hide the window when MTGO isn't the active window,
        # so the overlay never plasters win rates over other apps.
        _log.debug(
            "MTGO %s focus - overlay %s.",
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
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QSystemTrayIcon

        from .system.resources import resource_path

        icon_path = resource_path("assets/tray.ico")
        icon = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._tray = QSystemTrayIcon(icon, self.app)
        self._tray.setToolTip("MTGO Draft Helper")
        self._tray.setContextMenu(self._build_menu())
        self._tray.show()

    def _refresh_tray(self) -> None:
        """Rebuild the menu so state-dependent items (e.g. the CSV action, gated
        on ``use_live_17lands``) reflect settings changed after launch."""
        if self._tray is not None:
            self._tray.setContextMenu(self._build_menu())

    def _build_menu(self):
        from PySide6.QtGui import QAction, QActionGroup
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        menu.setToolTipsVisible(True)

        # Top-level 17Lands credit + link to the data's source page, per their
        # usage guidelines (kept visible, not buried in a submenu/footnote).
        credit = QAction("Win-rate data from 17Lands", menu)
        credit.setToolTip("Opens 17Lands' card data page in your browser.")
        credit.triggered.connect(self._open_17lands)
        menu.addAction(credit)
        menu.addSeparator()

        # Connection setup - the things the overlay needs to find drafts.
        for text, slot in (
            ("Enter MTGO username", self._prompt_username),
            ("Change log folder", self._prompt_log_folder),
            ("Setup status…", self._show_setup_status),
        ):
            action = QAction(text, menu)
            action.triggered.connect(slot)
            menu.addAction(action)

        menu.addSeparator()
        # Ratings / win-rate sources, grouped together.
        # Held on self so the action group (and its checked state) outlives this
        # method; mutually-exclusive checkable picks for the win-rate cohort.
        self._group_actions = QActionGroup(menu)
        self._group_actions.setExclusive(True)
        # The top/all cohort split is a live-endpoint parameter; a user CSV export
        # is a single cohort, so grey the picks out whenever the CSV is the source.
        cohort_from_csv = self._ratings_from_csv()
        for label, group in (
            ("Win rates: Top players", GROUP_TOP),
            ("Win rates: All players", GROUP_ALL),
        ):
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(self.settings.user_group == group)
            action.triggered.connect(lambda _checked, g=group: self._set_user_group(g))
            if cohort_from_csv:
                action.setEnabled(False)
                action.setToolTip(
                    "The top/all-players split needs live 17Lands data; a CSV "
                    "export covers a single cohort."
                )
            self._group_actions.addAction(action)
            menu.addAction(action)

        # With the live endpoint on, the CSV is only an automatic offline
        # fallback - so the picker is greyed out rather than presented as a source.
        csv_action = QAction("Set ratings CSV", menu)
        csv_action.triggered.connect(self._prompt_ratings_csv)
        if self.settings.use_live_17lands:
            csv_action.setEnabled(False)
            csv_action.setToolTip(
                "Using live 17Lands data; the CSV is only a fallback when offline."
            )
        menu.addAction(csv_action)

        prices_action = QAction("Show ticket prices", menu)
        prices_action.setCheckable(True)
        prices_action.setChecked(self.settings.show_prices)
        prices_action.setToolTip(
            "Draw each card's MTGO ticket price (from Scryfall) below its win rate."
        )
        prices_action.triggered.connect(self._toggle_prices)
        menu.addAction(prices_action)

        download_action = QAction("Download set…", menu)
        download_action.triggered.connect(self._prompt_download_set)
        menu.addAction(download_action)

        menu.addSeparator()
        clear_action = QAction("Clear local data…", menu)
        clear_action.triggered.connect(self._prompt_clear_data)
        menu.addAction(clear_action)

        menu.addSeparator()
        update_action = QAction("Check for updates…", menu)
        update_action.triggered.connect(lambda: self._check_for_updates(quiet=False))
        menu.addAction(update_action)

        about_action = QAction("About", menu)
        about_action.triggered.connect(self._show_about)
        menu.addAction(about_action)

        exit_action = QAction("Exit", menu)
        exit_action.triggered.connect(self._exit)
        menu.addAction(exit_action)

        return menu

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
        # The live ratings warm is the same scrape the embargo gates, so a manual
        # download of a new set still skips ratings (art is fine) until it lifts.
        live = not self._live_blocked(expansion)
        ratings_note = "ratings" if live else "art only (ratings embargoed)"
        _log.info(
            "Manual download requested for %s (art + %s).", expansion, ratings_note
        )
        if self._tray is not None:
            self._tray.showMessage(
                "MTGO Draft Helper",
                f"Downloading {expansion} card {ratings_note} - first run can take a "
                f"minute or two; watch the terminal/log for progress.",
            )
        self.pool.start(
            _PrefetchWorker(
                expansion,
                fmt,
                self.repo,
                self.price_repo,
                self._on_prefetch_done,
                start_date=self._start_date_for(expansion),
                use_live=live,
            )
        )

    def _on_prefetch_done(self, result: dict) -> None:
        if result.get("ok"):
            if result.get("ratings_ok"):
                ratings = "ratings cached"
            elif result.get("embargoed"):
                ratings = "ratings embargoed"
            else:
                ratings = "ratings unavailable"
            msg = (
                f"{result['expansion']} ready - {result['count']} card(s) art cached, "
                f"{ratings}."
            )
        else:
            msg = f"{result['expansion']} download failed: {result.get('error', 'see log')}"
        _log.info("%s", msg)
        if self._tray is not None:
            self._tray.showMessage("MTGO Draft Helper", msg)

    def _prompt_ratings_csv(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            None,
            "Select 17Lands card_ratings CSV",
            "",
            "CSV files (*.csv);;All files (*)",
        )
        if path:
            self.settings.manual_csv_path = path
            self.settings.save()
            _log.info("Ratings CSV set to %s.", path)
            self._reimport_ratings()

    def _set_user_group(self, group: str) -> None:
        """Switch the pill between the top-players and all-players cohorts. Both
        caches were warmed at draft prep, so re-recognizing reads the other one
        instantly; ``_reimport_ratings`` also re-warms in case a cache is missing."""
        if group == self.settings.user_group:
            return
        self.settings.user_group = group
        self.settings.save()
        _log.info("Win rate source set to %s players.", group)
        self._reimport_ratings()

    def _toggle_prices(self, checked: bool) -> None:
        """Show/hide the ticket-price pill. Re-runs draft prep (which warms the
        price cache when turning on) + recognition so the change lands on the
        current pack without waiting for the next one."""
        self.settings.show_prices = checked
        self.settings.save()
        _log.info("Ticket prices %s.", "enabled" if checked else "disabled")
        self._refresh_tray()
        self._reimport_ratings()

    def _reimport_ratings(self) -> None:
        """Rebuild the ratings cache from the current CSV and re-recognize, so new
        win rates *and* their set-relative colors take effect without a new draft."""
        if self.log is None or not self.log.current_pack:
            return
        _log.info("Re-importing ratings for the active %s pack.", self.expansion)
        worker = _EnsureWorker(
            self.repo,
            self.price_repo,
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

    def _show_setup_status(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if self.settings.use_live_17lands:
            ratings = "live 17Lands"
        elif self.settings.manual_csv_path:
            ratings = f"CSV ({self.settings.manual_csv_path})"
        else:
            ratings = "not set"
        lines = (
            f"MTGO username: {self.settings.mtgo_username or 'not set'}",
            f"Log folder: {self.settings.log_dir or 'not set'}",
            f"Privacy notice accepted: {'yes' if self.settings.accepted_disclaimer else 'no'}",
            f"Ratings source: {ratings}",
        )
        missing = self._missing_setup()
        box = QMessageBox()
        box.setWindowTitle("Setup status")
        box.setText("Ready to track drafts." if not missing else "Setup is incomplete.")
        box.setInformativeText("\n".join(lines))
        run_btn = box.addButton("Run setup wizard", QMessageBox.AcceptRole)
        box.addButton("Close", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is run_btn:
            run_onboarding(self.settings)
            self._refresh_tray()
            self._restart_watcher()

    def _open_17lands(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl("https://www.17lands.com/card_data"))

    def _show_about(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QMessageBox

        repo = "https://github.com/g0dnerd/mtgo_overlay"
        box = QMessageBox()
        box.setWindowTitle("About MTGO Draft Helper")
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"<b>MTGO Draft Helper</b><br>Version {__version__}<br><br>"
            "Draws 17Lands Game-in-Hand win rates onto the MTGO draft pick view."
        )
        box.setInformativeText(
            "Not affiliated with, endorsed by, or sponsored by 17Lands or Wizards "
            "of the Coast.<br><br>"
            f'Open source (GPL-3.0): <a href="{repo}">{repo}</a><br><br>'
            "Win-rate data courtesy of 17Lands. Card data &amp; images courtesy of "
            "Scryfall.<br><br>"
            "MTGO Draft Helper is unofficial Fan Content permitted under the "
            "Fan Content Policy. Not approved/endorsed by Wizards. Portions of the "
            "materials used are property of Wizards of the Coast. "
            "&copy;Wizards of the Coast LLC."
        )
        box.exec()

    # --- updates -------------------------------------------------------------

    def _check_for_updates(self, quiet: bool = False) -> None:
        """Dispatch a background release check. ``quiet`` (startup) surfaces only
        when an update exists; the manual path reports every outcome."""
        self._update_quiet = quiet
        if not quiet:
            _log.info("Checking for updates (current version %s)...", __version__)
        self.pool.start(_UpdateCheckWorker(self._on_update_check))

    def _open_releases(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(updater.RELEASES_URL))

    def _on_update_check(self, result: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        quiet = getattr(self, "_update_quiet", False)
        if not result.get("ok"):
            _log.warning("Update check error: %s", result.get("error"))
            if not quiet:
                QMessageBox.information(
                    None,
                    "Check for updates",
                    "Could not check for updates. Please try again later.",
                )
            return

        info = result.get("info")
        if info is None or not updater.is_newer(info.version, __version__):
            _log.info("No update available (current %s).", __version__)
            if not quiet:
                QMessageBox.information(
                    None,
                    "Check for updates",
                    f"You're on the latest version (v{__version__}).",
                )
            return

        _log.info("Update available: v%s (current v%s).", info.version, __version__)
        if quiet:
            if self._tray is not None:
                self._tray.showMessage(
                    "MTGO Draft Helper - update available",
                    f"Version v{info.version} is available. Open the tray menu → "
                    "'Check for updates…' to install.",
                )
            return

        if not getattr(sys, "frozen", False):
            # A dev checkout can't self-install; point at the release page instead.
            box = QMessageBox()
            box.setWindowTitle("Update available")
            box.setText(
                f"Version v{info.version} is available (you have v{__version__})."
            )
            box.setInformativeText("Open the release page to download it?")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            if box.exec() == QMessageBox.Yes:
                self._open_releases()
            return

        box = QMessageBox()
        box.setWindowTitle("Update available")
        box.setText(
            f"Version v{info.version} is available (you have v{__version__})."
        )
        notes = info.body.strip()
        box.setInformativeText(
            (notes + "\n\n" if notes else "")
            + "Download and install it now? The app will close to finish the update."
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.Yes)
        if box.exec() != QMessageBox.Yes:
            return
        if self._tray is not None:
            self._tray.showMessage("MTGO Draft Helper", "Downloading update…")
        self.pool.start(_UpdateDownloadWorker(info, self._on_update_downloaded))

    def _on_update_downloaded(self, result: dict) -> None:
        from PySide6.QtWidgets import QMessageBox

        if not result.get("ok"):
            _log.warning("Update download error: %s", result.get("error"))
            box = QMessageBox()
            box.setWindowTitle("Update failed")
            box.setText("The update could not be downloaded.")
            box.setInformativeText("Open the release page to download it manually?")
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.Yes)
            if box.exec() == QMessageBox.Yes:
                self._open_releases()
            return

        QMessageBox.information(
            None,
            "Update downloaded",
            "Update downloaded. The installer will open and the app will close.",
        )
        try:
            updater.launch_installer(result["path"])
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not launch installer: %s", exc)
            self._open_releases()
            return
        self._exit()

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
                "MTGO Draft Helper",
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
    _log.info("=== MTGO Draft Helper starting ===")
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
        "Startup complete - entering the Qt event loop."
        "The app lives in the system tray: use its menu to configure, then start a draft. "
        "Quit via the tray's Exit (or Ctrl+C)."
    )
    rc = app.exec()
    controller.shutdown()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
