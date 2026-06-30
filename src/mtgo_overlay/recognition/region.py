"""Detect the card grid in a screenshot, threshold-free where it counts.

The pixel stage (auto-Canny -> contours -> candidate rects) only needs to find
*most* cards; the geometry stage (robust size cluster + lattice fit + gap fill)
turns a noisy set of rects into a clean grid and synthesizes boxes for occluded
slots. The geometry helpers are pure and unit-tested with hand-built boxes; the
pixel stage is validated against real screenshot fixtures.
"""

from __future__ import annotations

import cv2
import numpy as np

from ..system.logging_setup import get_logger
from .config import RecognitionConfig
from .types import BBox, Slot

_log = get_logger("region")


def auto_canny(gray: np.ndarray, sigma: float) -> np.ndarray:
    median = float(np.median(gray))
    lo = int(max(0, (1.0 - sigma) * median))
    hi = int(min(255, (1.0 + sigma) * median))
    return cv2.Canny(gray, lo, hi)


def candidate_boxes(screen: np.ndarray, cfg: RecognitionConfig) -> list[BBox]:
    """Aspect/size-plausible card rects from edges. Noisy by design."""
    gray = cv2.cvtColor(screen, cv2.COLOR_BGR2GRAY) if screen.ndim == 3 else screen
    k = cfg.blur_ksize | 1
    blurred = cv2.GaussianBlur(gray, (k, k), 0)
    edges = auto_canny(blurred, cfg.canny_sigma)
    mk = cfg.morph_ksize | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (mk, mk))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    # RETR_LIST, not RETR_EXTERNAL: MTGO's near-black background means the
    # outermost contour is the whole screen; the cards are nested contours.
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    screen_area = float(gray.shape[0] * gray.shape[1])
    lo_area = cfg.min_card_area_frac * screen_area
    hi_area = cfg.max_card_area_frac * screen_area

    out: list[BBox] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if h == 0:
            continue
        if abs(w / h - cfg.mtg_aspect) > cfg.aspect_tol:
            continue
        if not (lo_area <= w * h <= hi_area):
            continue
        out.append(BBox(x, y, w, h))
    return out


def robust_size_filter(boxes: list[BBox], mad_factor: float) -> list[BBox]:
    """Drop boxes whose area is > ``mad_factor`` MADs from the modal card area."""
    if len(boxes) < 3:
        return list(boxes)
    areas = np.array([b.area for b in boxes], dtype=float)
    median = float(np.median(areas))
    mad = float(np.median(np.abs(areas - median)))
    if mad == 0.0:
        return [b for b, a in zip(boxes, areas) if a == median]
    return [b for b, a in zip(boxes, areas) if abs(a - median) <= mad_factor * mad]


def merge_overlapping(boxes: list[BBox], iou_thresh: float) -> list[BBox]:
    """Greedy NMS: keep the largest of any overlapping cluster of rects."""
    kept: list[BBox] = []
    for box in sorted(boxes, key=lambda b: b.area, reverse=True):
        if all(box.iou(k) < iou_thresh for k in kept):
            kept.append(box)
    return kept


def cluster_rows(boxes: list[BBox], tol: float) -> list[list[BBox]]:
    """Group boxes into rows by center-y proximity; sort rows top->bottom and
    each row left->right."""
    rows: list[list[BBox]] = []
    for box in sorted(boxes, key=lambda b: b.center[1]):
        if rows:
            mean_y = sum(b.center[1] for b in rows[-1]) / len(rows[-1])
            if abs(box.center[1] - mean_y) > tol:
                rows.append([box])
                continue
            rows[-1].append(box)
        else:
            rows.append([box])
    for row in rows:
        row.sort(key=lambda b: b.center[0])
    return rows


def fill_row_gaps(
    row: list[BBox], median_w: int, median_h: int
) -> list[tuple[BBox, bool]]:
    """Synthesize boxes for missing interior columns in a single row.

    Returns ``(box, synthetic)`` pairs left-to-right. Uses the median horizontal
    pitch between detected boxes; only fills *interior* gaps, never extrapolates
    past the row's ends (so we don't hallucinate cards outside the pack).
    """
    if len(row) < 2:
        return [(b, False) for b in row]
    centers = [b.center[0] for b in row]
    diffs = np.diff(centers)
    # The tightest adjacent spacing is the true column pitch; a gap is a multiple
    # of it (the median would sit between pitch and gap and miss the gap).
    pitch = float(np.min(diffs)) if len(diffs) else float(median_w)
    pitch = max(pitch, 0.6 * median_w)

    out: list[tuple[BBox, bool]] = [(row[0], False)]
    for prev, cur in zip(row, row[1:]):
        gap = cur.center[0] - prev.center[0]
        n_missing = min(int(round(gap / pitch)) - 1, 10)
        for k in range(1, n_missing + 1):
            cx = prev.center[0] + k * pitch
            cy = (prev.center[1] + cur.center[1]) / 2.0
            out.append(
                (
                    BBox(
                        int(cx - median_w / 2),
                        int(cy - median_h / 2),
                        median_w,
                        median_h,
                    ),
                    True,
                )
            )
        out.append((cur, False))
    return out


def detect_slots(
    screen: np.ndarray, cfg: RecognitionConfig, expected_count: int
) -> list[Slot]:
    """Full pipeline: edges -> candidates -> clean grid of :class:`Slot`."""
    boxes = candidate_boxes(screen, cfg)
    boxes = merge_overlapping(boxes, cfg.merge_iou)
    boxes = robust_size_filter(boxes, cfg.size_mad_factor)
    if not boxes:
        _log.warning("No card candidates found.")
        return []

    median_w = int(np.median([b.w for b in boxes]))
    median_h = int(np.median([b.h for b in boxes]))
    rows = cluster_rows(boxes, tol=cfg.row_tol_frac * median_h)

    slots: list[Slot] = []
    for r_idx, row in enumerate(rows):
        for c_idx, (box, synthetic) in enumerate(
            fill_row_gaps(row, median_w, median_h)
        ):
            slots.append(Slot(bbox=box, row=r_idx, col=c_idx, synthetic=synthetic))

    if expected_count and len(slots) != expected_count:
        _log.warning(
            "Detected %d slots but pack has %d names; assignment will reconcile.",
            len(slots),
            expected_count,
        )
    return slots
