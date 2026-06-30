"""region.py — pure geometry helpers (now) + pixel detection (synthetic + real)."""

from __future__ import annotations

import pytest

from mtgo_overlay.recognition import region
from mtgo_overlay.recognition.config import RecognitionConfig
from mtgo_overlay.recognition.eval import GroundTruth, slot_precision_recall
from mtgo_overlay.recognition.types import BBox

from conftest import discover_screenshot_fixtures


# --- pure geometry helpers (no image needed) -------------------------------

def test_robust_size_filter_drops_outliers():
    boxes = [BBox(0, 0, 100, 140) for _ in range(6)]
    boxes.append(BBox(0, 0, 400, 560))  # ~16x area outlier
    kept = region.robust_size_filter(boxes, mad_factor=3.0)
    assert BBox(0, 0, 400, 560) not in kept
    assert len(kept) == 6


def test_merge_overlapping_keeps_largest():
    big = BBox(0, 0, 100, 140)
    nested = BBox(2, 2, 96, 136)  # high IoU with big
    apart = BBox(300, 0, 100, 140)
    kept = region.merge_overlapping([nested, big, apart], iou_thresh=0.45)
    assert big in kept and apart in kept and nested not in kept


def test_cluster_rows_groups_and_sorts():
    row0 = [BBox(300, 0, 100, 140), BBox(0, 5, 100, 140), BBox(150, 0, 100, 140)]
    row1 = [BBox(0, 300, 100, 140), BBox(150, 305, 100, 140)]
    rows = region.cluster_rows(row0 + row1, tol=70)
    assert len(rows) == 2
    # First row sorted left->right.
    assert [b.x for b in rows[0]] == [0, 150, 300]
    assert [b.x for b in rows[1]] == [0, 150]


def test_fill_row_gaps_synthesizes_missing_interior():
    # Columns at x-centers 50, 150, [250 missing], 350. Pitch=100 from the
    # adjacent 50->150 pair; the 150->350 gap is 2x pitch -> one synthesized box.
    row = [BBox(0, 0, 100, 140), BBox(100, 0, 100, 140), BBox(300, 0, 100, 140)]
    filled = region.fill_row_gaps(row, median_w=100, median_h=140)
    assert len(filled) == 4
    assert [synthetic for _, synthetic in filled] == [False, False, True, False]
    mid_box = filled[2][0]
    assert 240 <= mid_box.center[0] <= 260  # synthesized near the gap center


def test_fill_row_gaps_single_box_noop():
    row = [BBox(0, 0, 100, 140)]
    assert region.fill_row_gaps(row, 100, 140) == [(row[0], False)]


# --- lattice reconstruction (uses the known count to recover row-end cards) --

def test_reconstruct_grid_extends_short_last_row():
    # The live [8,2] failure: top row fully detected (8), bottom row only the two
    # middle cards detected (cols 3,4). Knowing it's 14, the lattice must fill the
    # bottom to 6 INCLUDING the row-end cells fill_row_gaps could never reach.
    w, h = 90, 130
    top = [BBox(c * 100, 0, w, h) for c in range(8)]
    bottom = [BBox(3 * 100, 300, w, h), BBox(4 * 100, 300, w, h)]
    slots = region.reconstruct_grid([top, bottom], w, h, expected_count=14)

    assert slots is not None and len(slots) == 14
    row1 = [s for s in slots if s.row == 1]
    assert len(row1) == 6
    assert sorted(s.col for s in row1 if s.synthetic) == [0, 1, 2, 5]
    assert sorted(s.col for s in row1 if not s.synthetic) == [3, 4]


def test_reconstruct_grid_complete_grid_has_no_synthesis():
    w, h = 90, 130
    top = [BBox(c * 100, 0, w, h) for c in range(8)]
    bottom = [BBox(c * 100, 300, w, h) for c in range(6)]
    slots = region.reconstruct_grid([top, bottom], w, h, expected_count=14)
    assert slots is not None and len(slots) == 14
    assert not any(s.synthetic for s in slots)


def test_reconstruct_grid_bails_on_inconsistent_count():
    # A mid-resize frame: the wide row's right half is genuinely off-screen, so
    # 14 can't tile the detected geometry -> None (caller falls back; we do NOT
    # hallucinate the missing cards).
    w, h = 90, 130
    top = [BBox(c * 100, 0, w, h) for c in range(4)]
    bottom = [BBox(c * 100, 300, w, h) for c in range(6)]
    assert region.reconstruct_grid([top, bottom], w, h, expected_count=14) is None


# --- pixel stage on a synthetic grid (controlled, deterministic) -----------

def test_detect_slots_on_synthetic_grid(make_grid, distinct_tiles):
    img, boxes, _ = make_grid(distinct_tiles, rows=3, cols=5)
    slots = region.detect_slots(img, RecognitionConfig(), expected_count=15)

    predicted = [s.bbox for s in slots]
    truth = [BBox(*b) for b in boxes]
    precision, recall = slot_precision_recall(predicted, truth, iou_thresh=0.5)
    # Lenient: the pixel stage should recover most of a clean synthetic grid.
    assert recall >= 0.8, f"recall={recall:.2f} precision={precision:.2f}"


# --- real-screenshot region accuracy (activates when you add a fixture) -----

_FIXTURES = discover_screenshot_fixtures()


@pytest.mark.skipif(not _FIXTURES, reason="no real screenshot fixtures yet")
@pytest.mark.parametrize("png,gt_path", _FIXTURES)
def test_detect_slots_on_real_screenshot(png, gt_path):
    import cv2

    screen = cv2.imread(str(png))
    gt = GroundTruth.load(gt_path)
    slots = region.detect_slots(screen, RecognitionConfig(), len(gt.cards))
    precision, recall = slot_precision_recall(
        [s.bbox for s in slots], gt.boxes, iou_thresh=0.5
    )
    # Tune thresholds against your real data; start by just asserting it finds most.
    assert recall >= 0.7, f"recall={recall:.2f} precision={precision:.2f} on {png.name}"
