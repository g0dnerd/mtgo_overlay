"""Metrics for recognition fixtures: slot precision/recall, ID accuracy, px error.

Ground-truth JSON schema (one per screenshot fixture)::

    {
      "expansion": "MH3",
      "format": "PremierDraft",
      "screen_size": [W, H],
      "cards": [{"name": "Fanged Flames", "bbox": [x, y, w, h]}, ...]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .types import BBox, CardLocation


@dataclass(frozen=True)
class GroundTruth:
    expansion: str
    fmt: str
    screen_size: tuple[int, int]
    # A list, not a dict: a pack can legitimately contain duplicate card names.
    cards: list[tuple[str, BBox]]

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.cards]

    @property
    def boxes(self) -> list[BBox]:
        return [box for _, box in self.cards]

    @classmethod
    def load(cls, path: str | Path) -> "GroundTruth":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cards = [(c["name"], BBox(*c["bbox"])) for c in data["cards"]]
        size = tuple(data.get("screen_size", [0, 0]))
        return cls(
            expansion=data.get("expansion", ""),
            fmt=data.get("format", ""),
            screen_size=(int(size[0]), int(size[1])),
            cards=cards,
        )


def slot_precision_recall(
    predicted: list[BBox], truth: list[BBox], iou_thresh: float = 0.5
) -> tuple[float, float]:
    """Box-level precision/recall at an IoU threshold (greedy 1-to-1 matching)."""
    matched_truth: set[int] = set()
    true_positives = 0
    for pred in predicted:
        best_j, best_iou = -1, 0.0
        for j, gt in enumerate(truth):
            if j in matched_truth:
                continue
            score = pred.iou(gt)
            if score > best_iou:
                best_iou, best_j = score, j
        if best_j >= 0 and best_iou >= iou_thresh:
            matched_truth.add(best_j)
            true_positives += 1
    precision = true_positives / len(predicted) if predicted else 0.0
    recall = true_positives / len(truth) if truth else 0.0
    return precision, recall


def identification_accuracy(
    located: list[CardLocation], truth: GroundTruth, iou_thresh: float = 0.5
) -> float:
    """Fraction of ground-truth cards whose name is placed on the right box.

    Duplicate-aware: each ground-truth card is consumed by at most one located
    card (greedy match on name + IoU), so two copies of a card must be placed on
    both boxes to score full marks.
    """
    if not truth.cards:
        return 0.0
    remaining = list(truth.cards)
    correct = 0
    for loc in located:
        for i, (name, box) in enumerate(remaining):
            if name == loc.name and loc.bbox.iou(box) >= iou_thresh:
                correct += 1
                remaining.pop(i)
                break
    return correct / len(truth.cards)


def mean_center_error(
    located: list[CardLocation], truth: GroundTruth
) -> float | None:
    """Mean Euclidean px error between located and ground-truth box centers."""
    remaining = list(truth.cards)
    errors: list[float] = []
    for loc in located:
        best_i, best_d = -1, None
        for i, (name, box) in enumerate(remaining):
            if name != loc.name:
                continue
            (px, py), (gx, gy) = loc.bbox.center, box.center
            d = ((px - gx) ** 2 + (py - gy) ** 2) ** 0.5
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        if best_i >= 0:
            errors.append(best_d)
            remaining.pop(best_i)
    if not errors:
        return None
    return sum(errors) / len(errors)
