"""pipeline.py — wiring (synthetic, injected) + real screenshot (skip until ready)."""

from __future__ import annotations

import os

import pytest

from mtgo_overlay.recognition import pipeline, reference
from mtgo_overlay.recognition.config import RecognitionConfig
from mtgo_overlay.recognition.types import BBox, CardLocation, Slot

from conftest import discover_screenshot_fixtures


def test_locate_cards_wiring(make_grid, distinct_tiles):
    # Disable the match floor: synthetic noise tiles (with a border the bare
    # template lacks) don't score like real card art, and this exercises wiring,
    # not the floor (see test_identify.test_min_affinity_drops_low_scores).
    cfg = RecognitionConfig(min_affinity=-1.0)
    img, boxes, tile_ids = make_grid(distinct_tiles, rows=3, cols=5)
    names = [f"tile_{i}" for i in range(len(boxes))]

    # Stub detection: return the true boxes as slots (tests crop+identify+assign).
    def fake_detect(screen, _cfg, _expected):
        return [Slot(BBox(*b), row=i // 5, col=i % 5) for i, b in enumerate(boxes)]

    def templates_provider(name):
        idx = int(name.split("_")[1])
        tile = distinct_tiles[tile_ids[idx]]
        return [reference.prepare(tile, cfg.template_size, mode=cfg.prep_mode)]

    located = pipeline.locate_cards(
        img,
        names,
        "TST",
        cfg,
        detect=fake_detect,
        templates_provider=templates_provider,
    )

    assert len(located) == len(boxes)
    box_to_index = {b: i for i, b in enumerate(boxes)}
    for loc in located:
        idx = box_to_index[loc.bbox.as_tuple()]
        assert loc.name == f"tile_{idx}", f"{loc.name} mislocated at box {idx}"


def test_locate_cards_attaches_matched_printing_id():
    import numpy as np

    from mtgo_overlay.recognition import reference

    cfg = RecognitionConfig(min_affinity=-1.0)
    rng = np.random.default_rng(3)
    tile = rng.integers(0, 256, size=(140, 100, 3), dtype=np.uint8)
    decoy = rng.integers(0, 256, size=(140, 100, 3), dtype=np.uint8)
    screen = np.zeros((200, 200, 3), dtype=np.uint8)
    screen[10:150, 10:110] = tile  # the slot is a pixel copy of the 2nd printing

    def fake_detect(screen, _cfg, _expected):
        return [Slot(BBox(10, 10, 100, 140), row=0, col=0)]

    def templates_provider(name):
        return [
            reference.prepare(decoy, cfg.template_size, mode=cfg.prep_mode),
            reference.prepare(tile, cfg.template_size, mode=cfg.prep_mode),
        ]

    located = pipeline.locate_cards(
        screen,
        ["Card"],
        "TST",
        cfg,
        detect=fake_detect,
        templates_provider=templates_provider,
        ids_provider=lambda name: ["decoy-id", "match-id"],
    )
    assert len(located) == 1
    assert located[0].printing_id == "match-id"  # id of the winning template


def test_locate_cards_printing_id_none_when_ids_out_of_range():
    import numpy as np

    from mtgo_overlay.recognition import reference

    cfg = RecognitionConfig(min_affinity=-1.0)
    rng = np.random.default_rng(4)
    tile = rng.integers(0, 256, size=(140, 100, 3), dtype=np.uint8)
    screen = np.zeros((200, 200, 3), dtype=np.uint8)
    screen[10:150, 10:110] = tile

    located = pipeline.locate_cards(
        screen,
        ["Card"],
        "TST",
        cfg,
        detect=lambda *_: [Slot(BBox(10, 10, 100, 140), row=0, col=0)],
        templates_provider=lambda name: [
            reference.prepare(tile, cfg.template_size, mode=cfg.prep_mode)
        ],
        ids_provider=lambda name: [],  # provider disagrees with templates
    )
    assert located[0].printing_id is None  # bounds-guarded, no IndexError


def test_locate_cards_empty_when_no_slots():
    import numpy as np

    img = np.zeros((100, 100, 3), dtype="uint8")
    out = pipeline.locate_cards(
        img, ["A"], "TST", detect=lambda *_: [], templates_provider=lambda n: []
    )
    assert out == []


def test_get_pos_and_names_shape(monkeypatch):
    fake = [
        CardLocation("Fanged Flames", BBox(10, 20, 100, 140), 0.9),
        CardLocation("Drowner of Truth", BBox(140, 20, 100, 140), 0.8),
    ]
    monkeypatch.setattr(pipeline, "locate_cards", lambda *a, **k: fake)
    out = pipeline.get_pos_and_names("MH3", object(), ["Fanged Flames"])
    assert out == {
        "Fanged Flames": (10, 20, 100, 140),
        "Drowner of Truth": (140, 20, 100, 140),
    }


_FIXTURES = discover_screenshot_fixtures()


@pytest.mark.skipif(
    not _FIXTURES or not os.environ.get("MTGO_OVERLAY_LIVE_SCRYFALL"),
    reason="set MTGO_OVERLAY_LIVE_SCRYFALL=1 to run live identification (hits Scryfall)",
)
@pytest.mark.parametrize("png,gt_path", _FIXTURES)
def test_identify_on_real_screenshot(png, gt_path):
    import cv2

    from mtgo_overlay.recognition.eval import GroundTruth, identification_accuracy

    gt = GroundTruth.load(gt_path)
    screen = cv2.imread(str(png))
    located = pipeline.locate_cards(screen, gt.names, gt.expansion)
    accuracy = identification_accuracy(located, gt, iou_thresh=0.5)
    assert accuracy >= 0.8, f"identification accuracy {accuracy:.2f} on {png.name}"
