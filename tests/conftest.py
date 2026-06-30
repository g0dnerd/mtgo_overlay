from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen QApplication for widget tests."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def discover_screenshot_fixtures() -> list[tuple[Path, Path]]:
    """Find (screenshot.png, groundtruth.json) pairs under fixtures/<set>/.

    Returns an empty list until the user drops in real MTGO screenshots, which is
    what gates the recognition accuracy tests.
    """
    pairs: list[tuple[Path, Path]] = []
    for png in sorted(FIXTURES.glob("*/*.png")):
        if png.parent.name == "art":
            continue
        gt = png.with_suffix(".json")
        if gt.exists():
            pairs.append((png, gt))
    return pairs


@pytest.fixture
def make_grid():
    """Build a synthetic 'pack' image: tiles laid out on a grid + their boxes.

    Used to exercise the pixel detection + pipeline wiring deterministically.
    NOT a stand-in for real-world accuracy (real screenshots do that).
    """

    def _make(tiles, rows, cols, *, tile_w=200, tile_h=280, gap=26, margin=32, bg=110):
        height = margin * 2 + rows * tile_h + (rows - 1) * gap
        width = margin * 2 + cols * tile_w + (cols - 1) * gap
        img = np.full((height, width, 3), bg, np.uint8)
        boxes: list[tuple[int, int, int, int]] = []
        tile_ids: list[int] = []
        k = 0
        for r in range(rows):
            for c in range(cols):
                x = margin + c * (tile_w + gap)
                y = margin + r * (tile_h + gap)
                tile = cv2.resize(tiles[k % len(tiles)], (tile_w, tile_h))
                img[y : y + tile_h, x : x + tile_w] = tile
                # Cards have black borders — that's the edge detection keys on.
                cv2.rectangle(img, (x, y), (x + tile_w - 1, y + tile_h - 1), (0, 0, 0), 4)
                boxes.append((x, y, tile_w, tile_h))
                tile_ids.append(k % len(tiles))
                k += 1
        return img, boxes, tile_ids

    return _make


@pytest.fixture
def distinct_tiles():
    """A list of visually distinct, deterministic BGR card-sized tiles."""
    rng = np.random.default_rng(7)
    tiles = []
    for _ in range(15):
        base = rng.integers(0, 256, size=(280, 200, 3), dtype=np.uint8)
        tiles.append(cv2.GaussianBlur(base, (9, 9), 0))
    return tiles
