"""End-to-end: screenshot + pack names -> located cards.

``locate_cards`` wires region detection, template preparation and assignment. The
detector and template provider are injectable so the pipeline is unit-testable
without the real Scryfall integration (tests pass fixture templates), and so a
warmed cache can be swapped in cheaply.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from ..system.logging_setup import get_logger
from . import identify, reference, region
from .config import RecognitionConfig
from .types import BBox, CardLocation, Slot

_log = get_logger("recognition")

DetectFn = Callable[[np.ndarray, RecognitionConfig, int], list[Slot]]
TemplateProvider = Callable[[str], Sequence[np.ndarray]]


def _crop(screen: np.ndarray, bbox: BBox) -> np.ndarray:
    x = max(0, bbox.x)
    y = max(0, bbox.y)
    return screen[y : bbox.y + bbox.h, x : bbox.x + bbox.w]


def locate_cards(
    screen: np.ndarray,
    names: list[str],
    expansion: str,
    cfg: RecognitionConfig | None = None,
    *,
    cache_dir: Path | None = None,
    detect: DetectFn = region.detect_slots,
    templates_provider: TemplateProvider | None = None,
) -> list[CardLocation]:
    """Locate each pack card in ``screen``. Returns one entry per assigned slot."""
    cfg = cfg or RecognitionConfig()
    slots = detect(screen, cfg, len(names))
    if not slots:
        return []

    slot_images = [
        reference.prepare(_crop(screen, s.bbox), cfg.template_size, mode=cfg.prep_mode)
        for s in slots
    ]

    if templates_provider is None:
        def templates_provider(name: str):  # noqa: E306 - local default
            return reference.reference_templates(
                expansion, name, cfg.template_size, cache_dir=cache_dir, mode=cfg.prep_mode
            )

    scores = identify.build_score_matrix(slot_images, names, templates_provider)
    pairs = identify.assign(scores, min_affinity=cfg.min_affinity)
    if _log.isEnabledFor(logging.DEBUG):
        _log_confidence(slots, names, scores, pairs)
    return [
        CardLocation(name=names[j], bbox=slots[i].bbox, score=score)
        for i, j, score in pairs
    ]


def _log_confidence(
    slots: list[Slot],
    names: list[str],
    scores: np.ndarray,
    pairs: list[tuple[int, int, float]],
) -> None:
    """Per-slot assignment confidence: the assigned name+score vs the best
    unconstrained match (so a slot the assignment had to compromise on stands
    out). Only built when DEBUG logging is on."""
    assigned = {i: (names[j], s) for i, j, s in pairs}
    for i, slot in enumerate(slots):
        if scores.shape[1]:
            top_j = int(np.argmax(scores[i]))
            best = f"{names[top_j]}={scores[i][top_j]:.3f}"
        else:
            best = "n/a"
        if i in assigned:
            name, score = assigned[i]
            _log.debug(
                "  slot r%dc%d -> %-28s score=%.3f (best match %s)",
                slot.row, slot.col, name, score, best,
            )
        else:
            _log.debug(
                "  slot r%dc%d UNASSIGNED (best match %s)", slot.row, slot.col, best
            )


def get_pos_and_names(
    expansion: str, screen: np.ndarray, names: list[str]
) -> dict[str, tuple[int, int, int, int]]:
    """Compatibility shim matching the old ``rec.get_pos_and_names`` contract."""
    return {
        loc.name: loc.bbox.as_tuple()
        for loc in locate_cards(screen, names, expansion)
    }
