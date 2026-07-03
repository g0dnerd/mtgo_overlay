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


def robust_size_filter(
    boxes: list[BBox], mad_factor: float, rel_tol: float = 0.5
) -> list[BBox]:
    """Drop boxes whose area is far from the modal card area.

    Normally the band is ``mad_factor`` MADs wide. A clean uniform grid (the pack)
    makes >half the boxes share an *exact* area, collapsing the MAD to 0; there we
    fall back to a relative band (``rel_tol`` of the modal area) rather than exact
    equality, so a row that renders a pixel or two taller isn't silently dropped.
    """
    if len(boxes) < 3:
        return list(boxes)
    areas = np.array([b.area for b in boxes], dtype=float)
    median = float(np.median(areas))
    mad = float(np.median(np.abs(areas - median)))
    tol = mad_factor * mad if mad != 0.0 else rel_tol * median
    return [b for b, a in zip(boxes, areas) if abs(a - median) <= tol]


def merge_overlapping(boxes: list[BBox], iou_thresh: float) -> list[BBox]:
    """Greedy NMS: keep the largest of any overlapping cluster of rects."""
    kept: list[BBox] = []
    for box in sorted(boxes, key=lambda b: b.area, reverse=True):
        if all(box.iou(k) < iou_thresh for k in kept):
            kept.append(box)
    return kept


def select_pack_band(
    rows: list[list[BBox]], median_h: int, gap_frac: float
) -> list[list[BBox]]:
    """Keep the topmost contiguous band of rows, cutting at the first large gap.

    ``rows`` come top->bottom from :func:`cluster_rows`. The pack is always the top
    band in MTGO's draft view; the drafted pool sits below a wide vertical gap. Cut
    there so pool boxes can't pollute the modal-size filter or reach the assignment.
    The separator is the inter-row *edge* gap as a fraction of card height, so it's
    scale-invariant (pool cards can render at full pack-card size).
    """
    if not rows:
        return []
    kept = [rows[0]]
    for prev, cur in zip(rows, rows[1:]):
        prev_bottom = max(b.y2 for b in prev)
        cur_top = min(b.y for b in cur)
        if cur_top - prev_bottom > gap_frac * median_h:
            break
        kept.append(cur)
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


def reconstruct_grid(
    rows: list[list[BBox]], median_w: int, median_h: int, expected_count: int
) -> list[Slot] | None:
    """Fit a left-aligned lattice to ``rows`` and synthesize the *full* grid of
    ``expected_count`` cards, including row-end cells the pixel stage missed.

    MTGO lays the pack out left-aligned with a uniform column pitch; rows fill
    top-first so every row but the last holds ``C`` cards. Knowing the count lets
    us solve for ``C`` from the widest detected row and place every cell — unlike
    :func:`fill_row_gaps`, which only bridges *interior* gaps and so loses cards
    missing from a row's end. Returns ``None`` when the detected geometry can't
    form a consistent grid, so the caller falls back to per-row filling.
    """
    if not expected_count or not rows:
        return None
    all_boxes = [b for row in rows for b in row]
    if len(all_boxes) < 2:
        return None

    diffs = [
        nxt - cur
        for row in rows
        for cur, nxt in zip(
            sorted(b.center[0] for b in row),
            sorted(b.center[0] for b in row)[1:],
        )
        if nxt > cur
    ]
    if not diffs:
        return None
    # The tightest adjacent spacing is the true single-column pitch (a gap is a
    # multiple of it); clamp so noise can't collapse columns together.
    pitch = max(min(diffs), 0.6 * median_w)
    x0 = min(b.center[0] for b in all_boxes)
    n_rows = len(rows)

    placed: list[dict[int, BBox]] = []
    max_col = 0
    for row in rows:
        by_col: dict[int, BBox] = {}
        for b in row:
            col = int(round((b.center[0] - x0) / pitch))
            if col < 0:
                return None
            target = x0 + col * pitch
            prev = by_col.get(col)
            if prev is None or abs(b.center[0] - target) < abs(prev.center[0] - target):
                by_col[col] = b
            max_col = max(max_col, col)
        placed.append(by_col)

    cols = max_col + 1
    last_row = expected_count - (n_rows - 1) * cols
    if not (1 <= last_row <= cols):
        return None  # count can't tile this many rows at this width
    if any(col >= last_row for col in placed[-1]):
        return None  # a detected card sits past the short last row -> bad fit

    slots: list[Slot] = []
    for r, by_col in enumerate(placed):
        ncols = cols if r < n_rows - 1 else last_row
        row_y = sum(b.center[1] for b in rows[r]) / len(rows[r])
        for c in range(ncols):
            box = by_col.get(c)
            if box is not None:
                slots.append(Slot(bbox=box, row=r, col=c, synthetic=False))
            else:
                bx = int(round(x0 + c * pitch - median_w / 2))
                by = int(round(row_y - median_h / 2))
                slots.append(
                    Slot(BBox(bx, by, median_w, median_h), row=r, col=c, synthetic=True)
                )
    return slots


def _fill_rows_independently(
    rows: list[list[BBox]], median_w: int, median_h: int
) -> list[Slot]:
    """Fallback grid: bridge interior gaps per row, no count to lean on."""
    slots: list[Slot] = []
    for r_idx, row in enumerate(rows):
        filled = fill_row_gaps(row, median_w, median_h)
        n_syn = sum(1 for _b, syn in filled if syn)
        if n_syn:
            _log.debug(
                "Row %d: %d detected + %d synthesized -> %d columns.",
                r_idx,
                len(row),
                n_syn,
                len(filled),
            )
        for c_idx, (box, synthetic) in enumerate(filled):
            slots.append(Slot(bbox=box, row=r_idx, col=c_idx, synthetic=synthetic))
    return slots


def detect_slots(
    screen: np.ndarray, cfg: RecognitionConfig, expected_count: int
) -> list[Slot]:
    """Full pipeline: edges -> candidates -> clean grid of :class:`Slot`."""
    raw = candidate_boxes(screen, cfg)
    merged = merge_overlapping(raw, cfg.merge_iou)
    h, w = screen.shape[:2]
    if not merged:
        _log.warning("No card candidates found.")
        return []

    # Drop the drafted pool before the modal-size filter: the pool can outnumber
    # the pack and shift the median area onto pool cards, so band-split first.
    rough_h = int(np.median([b.h for b in merged]))
    all_rows = cluster_rows(merged, tol=cfg.row_tol_frac * rough_h)
    band = select_pack_band(all_rows, rough_h, cfg.band_gap_frac)
    band_boxes = [b for row in band for b in row]
    _log.debug(
        "Band split: %d row(s) detected -> kept top %d as pack (%d boxes).",
        len(all_rows),
        len(band),
        len(band_boxes),
    )

    boxes = robust_size_filter(band_boxes, cfg.size_mad_factor, cfg.size_rel_tol)
    _log.debug(
        "Candidates on %dx%d: %d raw -> %d after NMS -> %d in pack band -> %d after size filter.",
        w,
        h,
        len(raw),
        len(merged),
        len(band_boxes),
        len(boxes),
    )
    if not boxes:
        _log.warning("No card candidates found.")
        return []

    median_w = int(np.median([b.w for b in boxes]))
    median_h = int(np.median([b.h for b in boxes]))
    rows = cluster_rows(boxes, tol=cfg.row_tol_frac * median_h)
    _log.debug(
        "Median card %dx%d; %d row(s), detected columns per row: %s.",
        median_w,
        median_h,
        len(rows),
        [len(r) for r in rows],
    )

    slots = reconstruct_grid(rows, median_w, median_h, expected_count)
    if slots is not None:
        _log.debug(
            "Lattice fit: %d slots (%d synthesized).",
            len(slots),
            sum(s.synthetic for s in slots),
        )
    else:
        slots = _fill_rows_independently(rows, median_w, median_h)

    if expected_count and len(slots) != expected_count:
        _log.warning(
            "Detected %d slots but pack has %d names; assignment will reconcile.",
            len(slots),
            expected_count,
        )
    else:
        _log.debug("Detected %d slots (expected %d).", len(slots), expected_count)
    return slots
