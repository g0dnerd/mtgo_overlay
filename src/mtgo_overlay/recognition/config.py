"""Unitless / relative recognition config.

Every value is either a ratio, a tolerance, or a fraction of something present in
the image (screen area, median card size). There are deliberately NO absolute
pixel constants and no 1920x1080 assumptions — all scale comes from the cards
actually detected, so the same config works at any MTGO size or DPI.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecognitionConfig:
    # --- card shape ---
    mtg_aspect: float = 0.717          # width / height of an MTG card (63x88mm)
    aspect_tol: float = 0.13           # allowed deviation from mtg_aspect

    # --- edge detection (auto-Canny: lo=(1-sigma)*median, hi=(1+sigma)*median) ---
    canny_sigma: float = 0.33
    blur_ksize: int = 3                # odd; Gaussian pre-blur
    morph_ksize: int = 3               # odd; close gaps in edges

    # --- candidate sizing (fractions of screen area, robust to resolution) ---
    min_card_area_frac: float = 0.0015
    max_card_area_frac: float = 0.30
    size_mad_factor: float = 3.0       # reject boxes > N MADs from modal card area

    # --- de-dup + lattice ---
    merge_iou: float = 0.45            # NMS threshold for duplicate contours
    row_tol_frac: float = 0.5          # row grouping tol, as fraction of card height

    # --- template matching ---
    template_w: int = 100              # canonical template/slot width (px)
    template_h: int = 140              # canonical template/slot height (px) ~0.714
    prep_mode: str = "gray"            # "gray" or "gradient"
    min_affinity: float = -1.0         # soft floor on match score (-1 disables)

    @property
    def template_size(self) -> tuple[int, int]:
        return (self.template_w, self.template_h)
