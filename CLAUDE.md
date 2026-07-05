# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

A click-through overlay that draws 17Lands GIH win rates onto the MTGO draft pick
view. Card names come from MTGO's draft log; positions from OpenCV template
matching; ratings from 17Lands. New code lives under `src/mtgo_overlay/`.

## The two-OS model (read this first)

The repo lives on the **WSL filesystem**, is **edited from WSL**, but the app
**runs on Windows** (MTGO is Windows-only). This split is fundamental:

- **WSL** runs only the headless test suite + recognition dev tools. Anything
  touching Win32 (`system/win32.py`, `capture/`, live overlay, tray, `run.py`'s
  loop) raises `RuntimeError` or can't be exercised here - do not try to run the
  app or `run.py`'s full loop in WSL.
- **Windows** runs the real app and the PyInstaller build, via `uv` (also on PATH
  there), using a **separate env dir** so it doesn't clobber the WSL `.venv`:
  `$env:UV_PROJECT_ENVIRONMENT=".venv-win"`. `uv.lock` is cross-platform
  (`pywin32` resolves only on win32).

`uv` is the only package manager - never pip/poetry.

## Commands

```bash
# WSL: setup + headless tests (Qt tests REQUIRE the offscreen platform)
uv sync --extra dev
QT_QPA_PLATFORM=offscreen uv run pytest tests/ -q

# single test / file
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_region.py::test_cluster_rows_groups_and_sorts -q

# networked tests (rate-limited) are each gated behind an env flag
MTGO_OVERLAY_LIVE_SCRYFALL=1 QT_QPA_PLATFORM=offscreen uv run pytest tests/test_pipeline.py -q
MTGO_OVERLAY_LIVE_17LANDS=1 QT_QPA_PLATFORM=offscreen uv run pytest tests/test_data.py -q

# recognition dev loop in WSL (no MTGO, no Scryfall): detect + annotate a screenshot
uv run python tools/annotate_preview.py shot.png --expected 15 --boxes-only
```

```powershell
# Windows: run the app / build the exe
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"; uv sync --extra dev
uv run python run.py
.\build.ps1   # -> dist\MtgoOverlay.exe
```

Release (WSL): `scripts/release.sh [patch|minor|major|X.Y.Z] [-b BRANCH]` bumps
`__version__` on the feature branch, runs the tests, merges into `main`, and
pushes an annotated `vX.Y.Z` tag (which triggers the Windows build). Defaults to a
patch bump of the current branch; refuses a dirty tree or an existing tag.

There is no configured linter/formatter.

## Architecture

**Runtime data flow** (spans `app.py`, `draft/log_watcher.py`, `overlay/`):
`DraftLogWatcher` (watchdog thread) emits Qt signals → `AppController` (UI thread)
debounces and dispatches a `RecognitionWorker` on `QThreadPool` → the worker does
`capture_client_area` + `recognition.locate_cards` + `RatingsRepository.lookup`
and emits `labelsReady` (auto-queued back to the UI thread) → `AppController` maps
capture-px → logical coords → `OverlayWindow.set_labels`. A `WindowTracker`
(QTimer ~10 Hz) keeps the overlay pinned to MTGO and re-dispatches recognition on
resize. Stale recognition results are dropped by a monotonic generation counter.

**Recognition is assignment, not thresholding** (`recognition/`): the closed,
known pack name set + a regular card grid reframe "does ROI match card X above a
threshold?" into a 1-to-1 assignment problem. `region.detect_slots` finds the grid
(auto-Canny → **`RETR_LIST`** contours → robust modal-size cluster → lattice fit +
gap synthesis), `identify` builds a score matrix (`matchTemplate` of each slot vs
each name's Scryfall artworks) and solves it with `scipy.linear_sum_assignment`.
There are **no absolute pixel constants and no 1920×1080 assumptions** - every
value in `recognition/config.py` is a ratio/fraction, and all scale derives from
the cards actually detected (MTGO's draft region + card size are user-resizable).

**Coordinate model**: the overlay is pinned to MTGO's **client** origin
(`win32.get_client_rect_on_screen`), so a physical-pixel card box maps to overlay
coords by a pure divide-by-`devicePixelRatio` (`app.map_capture_to_logical`).
Label offsets are fractions of the card box, never pixels.

**`system/` is import-safe everywhere**: `win32.py` imports cleanly on Linux but
every Win32 call guards on `IS_WINDOWS`, so the package imports and the headless
tests run in WSL. Generated data (Scryfall art, ratings) goes to the writable
cache dir (`system/paths.py`); only shipped static assets use `resource_path`.

**Data layers**: `data/ratings_repo.py` is CSV-first by default (live 17Lands
endpoint is opt-in via `Settings.use_live_17lands`) with a 24h TTL keyed on an
embedded `fetched_at`. `recognition/scryfall_art.py` enumerates each card's
artworks via a Scryfall search and downloads them cache-first behind a 10 req/s
limiter; `ensure_set_artwork` warms the cache per draft so recognition stays
offline on the hot path. **Respect Scryfall's rate limit even when testing.**

## Conventions & gotchas

- **Qt in tests**: use the session `qapp` fixture (a single `QApplication`); never
  create a bare `QCoreApplication` (mixing the two segfaults widget tests). Render
  tests run under `QT_QPA_PLATFORM=offscreen`.
- **Testability seams**: hard-to-test edges are injected so logic stays
  WSL-testable - `pipeline.locate_cards(detect=…, templates_provider=…)`,
  `WindowTracker(find_hwnd=…, get_rect=…)`, `RatingsRepository(client=…, time_fn=…)`.
  Prefer extending these over reaching for the real Win32/network path in a test.
- **Recognition fixtures**: accuracy tests under `tests/fixtures/<set>/` auto-
  activate when a `*.png` + `*.json` ground-truth pair exists (see
  `discover_screenshot_fixtures`). Ground truth is a **list** of `(name, bbox)` -
  a pack can contain duplicate card names. Bootstrap new ones with
  `tools/propose_groundtruth.py`, then hand-correct.
- **Log parser** (`draft/log_parser.py`) is a faithful port - behavior unchanged.
  It assumes an **8-player pod** (12 fixed header lines) and derives the expansion
  from the last 3 chars of the filename before `.txt`.
- Tests are hermetic (no network); the only networked tests are the two
  env-gated live ones (`MTGO_OVERLAY_LIVE_SCRYFALL` identification,
  `MTGO_OVERLAY_LIVE_17LANDS` ratings fetch).
