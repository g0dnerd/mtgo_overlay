"""Locate bundled *static* assets (e.g. tray.ico).

This is the shim the old code was missing. The critical rule the rewrite
enforces: ``resource_path`` is ONLY for read-only assets shipped inside the
PyInstaller bundle. Generated data (Scryfall art, ratings caches) must go to the
writable cache dir (see :mod:`mtgo_overlay.system.paths`), never here — that
mix-up is what produced the old "resource path X missing -> fatal exit" class.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _dev_base() -> Path:
    # src/mtgo_overlay/system/resources.py -> repo root is parents[3]
    return Path(__file__).resolve().parents[3]


def resource_path(relative: str | Path) -> Path:
    """Absolute path to a bundled static asset.

    Works both from source (repo root) and from a PyInstaller one-file bundle
    (``sys._MEIPASS``).
    """
    base = getattr(sys, "_MEIPASS", None)
    root = Path(base) if base is not None else _dev_base()
    return root / relative
