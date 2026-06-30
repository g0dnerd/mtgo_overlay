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
    """Look of the GIH win-rate pills. Every size/offset is a *fraction of the card
    box*, not pixels, so the label scales with arbitrary MTGO sizes / DPI."""

    font_family: str = "Segoe UI"
    fg: str = "#ffffff"
    # Gray pill for cards with no rating (low sample / unknown).
    unknown_color: str = "#6b7280"
    # Number height as a fraction of card height.
    font_h_frac: float = 0.072
    # Pill's bottom edge as a fraction of card height — kept at the bottom of the
    # art, clear of the title bar above and the type line / rules text below.
    pill_bottom_frac: float = 0.50
    # Inset of the pill's right edge from the card's right edge (fraction of width).
    inset_x_frac: float = 0.045
    # Horizontal / vertical text padding as fractions of card width / height.
    pad_x_frac: float = 0.035
    pad_y_frac: float = 0.012


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
