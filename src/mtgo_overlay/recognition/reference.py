"""Prepare slot-sized grayscale (or gradient) templates from cached artwork.

Depends only on the :mod:`scryfall_art` contract (it consumes ``Path``s), so the
path->template preparation is unit-testable with fixture PNGs.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from ..system import paths
from . import scryfall_art


def prepare(
    image: np.ndarray, size: tuple[int, int], *, mode: str = "gray"
) -> np.ndarray:
    """Grayscale + resize an image to ``size`` (canonical slot size).

    ``mode="gradient"`` returns a normalized Sobel magnitude instead of raw
    grayscale - more robust to foil glare / color shifts, at some cost.
    """
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image
    resized = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    if mode == "gradient":
        gx = cv2.Sobel(resized, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(resized, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        return cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return resized


def load_template_image(path: str | Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Could not read template image: {path}")
    return img


def templates_from_paths(
    image_paths: tuple[Path, ...], size: tuple[int, int], *, mode: str = "gray"
) -> tuple[np.ndarray, ...]:
    """Load + prepare a set of artwork images into canonical-size templates."""
    return tuple(prepare(load_template_image(p), size, mode=mode) for p in image_paths)


@lru_cache(maxsize=4096)
def reference_templates(
    expansion: str,
    name: str,
    slot_size: tuple[int, int],
    *,
    cache_dir: Path | None = None,
    mode: str = "gray",
) -> tuple[np.ndarray, ...]:
    """All prepared templates for ``name`` in ``expansion`` (memoized).
    Enumerates booster-eligible artworks and prepares each cached image.
    """
    cache_dir = cache_dir or paths.scryfall_cache_dir()
    refs = scryfall_art.booster_artwork_ids(expansion, name, cache_dir=cache_dir)
    image_paths = tuple(scryfall_art.fetch_artwork(ref, cache_dir) for ref in refs)
    return templates_from_paths(image_paths, slot_size, mode=mode)
