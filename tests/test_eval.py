"""eval.py metrics - including duplicate card names in a pack."""

from __future__ import annotations

from mtgo_overlay.recognition.eval import (
    GroundTruth,
    identification_accuracy,
    slot_precision_recall,
)
from mtgo_overlay.recognition.types import BBox, CardLocation


def _gt(cards):
    return GroundTruth("MSH", "PremierDraft", (100, 100), cards)


def test_groundtruth_preserves_duplicate_names(fixtures_dir):
    gt = GroundTruth.load(fixtures_dir / "msh" / "pack1_pick1.json")
    assert len(gt.cards) == 14
    assert gt.names.count("Widow's Bite") == 2  # the pack really has two


def test_identification_accuracy_handles_duplicates():
    a, b = BBox(0, 0, 100, 140), BBox(300, 0, 100, 140)
    gt = _gt([("Widow's Bite", a), ("Widow's Bite", b)])

    both = [CardLocation("Widow's Bite", a, 0.9), CardLocation("Widow's Bite", b, 0.8)]
    assert identification_accuracy(both, gt) == 1.0

    # Both placed on the same box -> only one ground-truth card is satisfied.
    same = [CardLocation("Widow's Bite", a, 0.9), CardLocation("Widow's Bite", a, 0.8)]
    assert identification_accuracy(same, gt) == 0.5


def test_slot_precision_recall_basic():
    truth = [BBox(0, 0, 100, 140), BBox(300, 0, 100, 140)]
    pred = [BBox(2, 2, 100, 140)]  # overlaps the first only
    precision, recall = slot_precision_recall(pred, truth, iou_thresh=0.5)
    assert precision == 1.0
    assert recall == 0.5
