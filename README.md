# MTGO 17lands Overlay

A click-through, always-on-top overlay that draws 17lands **Game-in-Hand Win
Rate (GIH WR)** next to each card in the current Magic: The Gathering Online draft
pack. Card *names* come from MTGO's draft log; card *positions* come from OpenCV
template matching; *ratings* come from 17lands (manual CSV by default, with an
optional live fetch).

> Recognition, data, log, overlay, and the Scryfall artwork integration are built
> and unit-tested headless. The full recognition pipeline (region detection ->
> Scryfall template fetch -> Hungarian identification) is validated end-to-end at
> **100% on a real MTGO Marvel Super Heroes pack** (`tests/fixtures/msh/`). The
> Windows-only runtime glue is written but validated on Windows (see **Windows
> verification**).

## Architecture

```
src/mtgo_overlay/
  system/      resources, paths (%APPDATA%/%LOCALAPPDATA%), logging, win32 (DPI/click-through)
  config/      Settings + OverlayStyle dataclasses (TOML, atomic save)
  data/        17lands client + ratings repo (24h TTL, CSV fallback) + set/format enums
  draft/       log_parser (port) + log_watcher (watchdog -> Qt signals)
  capture/     MTGO client-area capture (mss + win32)
  recognition/ region (auto-Canny + lattice), identify (template + Hungarian),
               reference (template prep), scryfall_art (enum+cache), pipeline, eval
  overlay/     overlay_window (one click-through QWidget) + window_tracker
  app.py       AppController state machine + entrypoint
run.py         entrypoint (DPI -> QApplication -> AppController -> exec)
tools/         fake_mtgo, replay_log, annotate_preview, propose_groundtruth
tests/         headless tests + fixtures
```

## Dev setup (`uv`)

This repo lives on the WSL filesystem and is edited from WSL, but the app *runs*
on Windows (MTGO is Windows-only). Use a **separate uv environment per OS** so the
two don't clobber each other's `.venv`:

```bash
# WSL (tests, recognition dev, everything headless)
uv sync --extra dev
QT_QPA_PLATFORM=offscreen uv run pytest tests/ -q
```

```powershell
# Windows (the actual app + the build)
$env:UV_PROJECT_ENVIRONMENT = ".venv-win"
uv sync --extra dev
uv run python run.py
```

`uv.lock` is cross-platform (`pywin32` resolves only on Windows).

## Running

On Windows: `uv run python run.py`. A tray icon appears — set your **MTGO
username** and **log folder** from its menu. Start a draft; labels appear over the
pack and update each pick. Config is saved to `%APPDATA%\MtgoOverlay\config.toml`;
caches/logs live under `%LOCALAPPDATA%\MtgoOverlay\`.

## Ratings data (17lands)

Default is your locally-downloaded `card_ratings.csv` (17lands "download to CSV").
Point `manual_csv_path` at it (or use the tray once that's wired). An optional live
fetch of 17lands' internal `card_ratings/data` endpoint exists behind
`use_live_17lands = true`; it is **off by default** because that endpoint is
undocumented/internal — review 17lands' usage guidelines before enabling it.
`robots.txt` does not disallow the path; the repo caps usage at one request per
set/format per 24h and sends a polite identifying User-Agent.

## Recognition + Scryfall

`recognition/scryfall_art.py` enumerates each card's booster artworks via a
Scryfall search (`set:<exp> !"name" unique=prints`) and downloads them cache-first.
It reuses the old `crawler/fetch.py` primitives (10 req/s rate limit, cache-first
fetch, the `{name: [variants]}` model) and replaces the heavyweight
`bulk_data.json` + hand-authored `information.json` enumeration. `ensure_set_artwork`
warms the cache per draft so recognition stays offline on the hot path. Cache:
`%LOCALAPPDATA%\MtgoOverlay\cache\scryfall\` (`<EXP>_variants.json` + `<id>.png`).

> Known gap: enumeration queries only the draft set. Booster-fun treatments printed
> in *linked* sets aren't pulled yet — extend `_query_scryfall_prints` with the
> set's companions if a treatment is missed.

To add another real screenshot fixture (activates the recognition accuracy tests):

```bash
# 1. capture an MTGO draft pick-view screenshot -> tests/fixtures/mh3/pack1.png
# 2. bootstrap ground truth, then hand-correct names/boxes in the JSON:
uv run python tools/propose_groundtruth.py tests/fixtures/mh3/pack1.png \
    --expected 15 --expansion MH3 --out tests/fixtures/mh3/pack1.json
# 3. eyeball detection (no Scryfall needed):
uv run python tools/annotate_preview.py tests/fixtures/mh3/pack1.png --expected 15 --boxes-only
# 4. tests/test_region.py's accuracy test now runs against it.
```

## Testing tiers

- **Tier 1 (headless, WSL/CI):** `pytest tests/` — recognition core, data, log
  parser, overlay render (offscreen). No display, no MTGO, no network. Set
  `MTGO_OVERLAY_LIVE_SCRYFALL=1` to also run the live end-to-end identification
  test (hits Scryfall, rate-limited).
- **Tier 2 (WSL preview):** `tools/annotate_preview.py` on a screenshot.
- **Tier 3 (Windows, no MTGO):** `tools/fake_mtgo.py` + `tools/replay_log.py`.
- **Tier 4 (real MTGO):** manual pre-release smoke.

## Windows verification

Run these on Windows after `($env:UV_PROJECT_ENVIRONMENT=".venv-win"; uv sync --extra dev)`:

1. **Bootstrap:** `uv run python run.py` → tray icon appears, no crash.
2. **Fake MTGO:** `uv run python tools/fake_mtgo.py shot.png --geometry 1600x1000+200+100`,
   then `uv run python run.py` → overlay finds + pins to the window; a control under
   a label still receives clicks (click-through); overlay follows move/resize.
3. **Simulated draft:** with the fake window up, run `tools/replay_log.py` into your
   configured log folder → labels appear/update/clear per pack.
4. **Build:** `.\build.ps1` → `dist\MtgoOverlay.exe` runs from a clean path and
   writes config/cache/logs under `%APPDATA%`/`%LOCALAPPDATA%`.

## Build

`.\build.ps1` (PyInstaller one-file, no console, bundled `assets/`).
