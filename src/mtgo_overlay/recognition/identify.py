"""Identify slots by 1-to-1 assignment over the closed pack name set.

The closed, known name set + a regular grid turns "does this ROI match card X
above some threshold?" into an assignment problem: build a score matrix
``S[slot, name]`` and solve for the best global 1-to-1 mapping. Acceptance is
*relative* (the best mutual match wins), so there are no absolute thresholds,
only an optional soft floor to drop near-random slots.
"""

from __future__ import annotations

from typing import Callable, Sequence

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

TemplateProvider = Callable[[str], Sequence[np.ndarray]]


def match_score(slot: np.ndarray, template: np.ndarray) -> float:
    """Normalized correlation of two equal-size prepared images (in [-1, 1])."""
    result = cv2.matchTemplate(slot, template, cv2.TM_CCOEFF_NORMED)
    return float(result.max())


def build_score_matrix(
    slot_images: Sequence[np.ndarray],
    names: Sequence[str],
    get_templates: TemplateProvider,
) -> tuple[np.ndarray, np.ndarray]:
    """Score every slot against every name's artworks.

    Returns ``(scores, best_tpl)``: ``scores[i, j]`` is slot ``i``'s best match
    against any artwork of name ``j``, and ``best_tpl[i, j]`` is the index of the
    winning template (``-1`` when name ``j`` has no templates), so callers can
    recover *which* printing matched.
    """
    template_cache = {name: list(get_templates(name)) for name in names}
    scores = np.full((len(slot_images), len(names)), -1.0, dtype=np.float32)
    best_tpl = np.full((len(slot_images), len(names)), -1, dtype=np.int32)
    for i, slot in enumerate(slot_images):
        for j, name in enumerate(names):
            for t, template in enumerate(template_cache[name]):
                s = match_score(slot, template)
                if s > scores[i, j]:
                    scores[i, j] = s
                    best_tpl[i, j] = t
    return scores, best_tpl


def assign(
    scores: np.ndarray, *, min_affinity: float = -1.0
) -> list[tuple[int, int, float]]:
    """Optimal 1-to-1 slot->name assignment maximizing total score.

    Returns ``(slot_idx, name_idx, score)`` triples. Pairs scoring below
    ``min_affinity`` are dropped (a soft floor for near-random slots only).
    """
    if scores.size == 0:
        return []
    row_idx, col_idx = linear_sum_assignment(-scores)
    out: list[tuple[int, int, float]] = []
    for i, j in zip(row_idx, col_idx):
        score = float(scores[i, j])
        if score >= min_affinity:
            out.append((int(i), int(j), score))
    return out
