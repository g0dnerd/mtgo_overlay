"""User settings persisted as TOML at ``%APPDATA%\\MtgoOverlay\\config.toml``.

Replaces the old ``settings.ini`` + ad-hoc ``~/Documents\\settings.ini`` path.
``pathlib`` everywhere; saves are atomic (temp file + ``os.replace``).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import tomli_w

from ..system import paths


@dataclass
class OverlayStyle:
    """Look of the GIH labels. Offsets are *fractions of the card box*, not pixels,
    so they survive arbitrary MTGO sizes / DPI."""

    font_family: str = "Segoe UI"
    font_size_pt: int = 11
    fg: str = "#ffffff"
    bg: str = "#707070"
    # Inset of the label from the card's right edge, as a fraction of card width.
    inset_x_frac: float = 0.04
    # Vertical position of the label top, as a fraction of card height.
    top_y_frac: float = 0.05
    # Background opacity 0..1 (Qt per-pixel alpha; not Win32 LWA_ALPHA).
    bg_opacity: float = 0.85
    padding_px: int = 4


@dataclass
class Settings:
    mtgo_username: str = ""
    log_dir: str = ""
    # 17lands draft format (see data.sets.Format).
    fmt: str = "PremierDraft"
    # Default acquisition is the local CSV; flip on only after reviewing 17lands'
    # usage guidelines (the live endpoint is undocumented/internal).
    use_live_17lands: bool = False
    manual_csv_path: str = ""
    # Polite identifying UA for the 17lands endpoint (tool + contact).
    user_agent: str = "MtgoOverlay/0.2 (+https://github.com/; personal use)"
    overlay: OverlayStyle = field(default_factory=OverlayStyle)

    # --- persistence ---------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or paths.config_file()
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            raw: dict[str, Any] = tomllib.load(fh)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)} - {"overlay"}
        kwargs: dict[str, Any] = {k: raw[k] for k in known if k in raw}
        overlay_raw = raw.get("overlay")
        if isinstance(overlay_raw, dict):
            style_known = {f.name for f in fields(OverlayStyle)}
            kwargs["overlay"] = OverlayStyle(
                **{k: v for k, v in overlay_raw.items() if k in style_known}
            )
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, path: Path | None = None) -> Path:
        path = path or paths.config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as fh:
            tomli_w.dump(self.to_dict(), fh)
        os.replace(tmp, path)
        return path
