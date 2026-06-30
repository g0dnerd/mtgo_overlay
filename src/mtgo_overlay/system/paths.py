"""Resolve per-user config / cache / log directories.

On Windows these land under ``%APPDATA%`` (config) and ``%LOCALAPPDATA%``
(cache + logs), matching the locations PyInstaller-packaged apps are expected to
write to. On other platforms (WSL/Linux dev + CI) they fall back to XDG dirs so
the rest of the code is testable without Windows.

Set ``MTGO_OVERLAY_HOME`` to override the root entirely (portable mode / tests).
"""

from __future__ import annotations

import os
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


def scryfall_cache_dir() -> Path:
    return _ensure(cache_dir() / "scryfall")
