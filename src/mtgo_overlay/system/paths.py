"""Resolve per-user config / cache / log directories.

On Windows these land under ``%APPDATA%`` (config) and ``%LOCALAPPDATA%``
(cache + logs), matching the locations PyInstaller-packaged apps are expected to
write to. On other platforms (WSL/Linux dev + CI) they fall back to XDG dirs so
the rest of the code is testable without Windows.

Set ``MTGO_OVERLAY_HOME`` to override the root entirely (portable mode / tests).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

APP_NAME = "MtgoOverlay"

_ENV_OVERRIDE = "MTGO_OVERLAY_HOME"


def _home() -> Path:
    return Path(os.path.expanduser("~"))


def _appdata_root() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override) / "config"
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else _home() / ".config"


def _localappdata_root() -> Path:
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        return Path(override) / "local"
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local)
    xdg = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg) if xdg else _home() / ".cache"


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_dir() -> Path:
    return _ensure(_appdata_root() / APP_NAME)


def cache_dir() -> Path:
    return _ensure(_localappdata_root() / APP_NAME / "cache")


def logs_dir() -> Path:
    return _ensure(_localappdata_root() / APP_NAME / "logs")


def config_file() -> Path:
    return config_dir() / "config.toml"


def ratings_cache_dir() -> Path:
    return _ensure(cache_dir() / "ratings")


def prices_cache_dir() -> Path:
    return _ensure(cache_dir() / "prices")


def scryfall_cache_dir() -> Path:
    return _ensure(cache_dir() / "scryfall")


def clear_local_data() -> list[Path]:
    """Delete all generated local data — caches (ratings + Scryfall art), the
    persisted config, and logs + debug captures — and return the top-level
    paths removed.

    The caller must release any open handles first (notably the app log file,
    which Windows keeps locked while open). The directory skeleton is recreated
    lazily by the getters above on next use.
    """
    # Reference paths without the getters' ``_ensure`` side effect, so this stays
    # idempotent instead of recreating the very dirs it just deleted.
    local = _localappdata_root() / APP_NAME
    cfg = _appdata_root() / APP_NAME / "config.toml"
    removed: list[Path] = []
    for d in (local / "cache", local / "logs"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d)
    if cfg.exists():
        cfg.unlink()
        removed.append(cfg)
    return removed
