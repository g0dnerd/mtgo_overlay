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
    # Fallback band (fraction of modal area) when a uniform grid collapses the MAD
    # to 0; keeps cards differing by a pixel or two while still dropping junk.
    size_rel_tol: float = 0.5

    # --- de-dup + lattice ---
    merge_iou: float = 0.45            # NMS threshold for duplicate contours
    row_tol_frac: float = 0.5          # row grouping tol, as fraction of card height
    # Inter-row edge gap (x median card height) above which the band below is the
    # drafted pool, not more pack rows. Sits above the intra-pack gap (~0.026) and
    # below the tightest observed pack->pool gap (~0.28).
    band_gap_frac: float = 0.15

    # --- template matching ---
    template_w: int = 100              # canonical template/slot width (px)
    template_h: int = 140              # canonical template/slot height (px) ~0.714
    prep_mode: str = "gray"            # "gray" or "gradient"
    # Soft floor on match score: real cards score ~0.6-0.85, so this only drops a
    # synthesized cell that landed on background (a mislabel) without touching a
    # real match. -1 disables.
    min_affinity: float = 0.35

    @property
    def template_size(self) -> tuple[int, int]:
        return (self.template_w, self.template_h)
