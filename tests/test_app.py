"""AppController pure helpers: label formatting, coord mapping, the join."""

from __future__ import annotations

from mtgo_overlay.app import (
    build_label_specs,
    expansion_from_log_path,
    format_label,
    map_capture_to_logical,
)
from mtgo_overlay.data.ratings_repo import CardRating
from mtgo_overlay.recognition.types import BBox, CardLocation


def test_format_label():
    assert format_label(75.7) == "GIH 75.7"
    assert format_label(None) == "GIH N/A"


def test_map_capture_to_logical():
    assert map_capture_to_logical((100, 200, 120, 168), 1.0) == (100, 200, 120, 168)
    assert map_capture_to_logical((150, 300, 120, 160), 1.5) == (100, 200, 80, 107)


def test_build_label_specs_joins_and_filters():
    located = [
        CardLocation("Agent Phil Coulson", BBox(100, 100, 120, 168), 0.9),
        CardLocation("Island", BBox(300, 100, 120, 168), 0.8),  # basic, no rating
        CardLocation("Lowsample Nobody", BBox(500, 100, 120, 168), 0.7),
    ]
    # Island is filtered upstream (lookup drops basics), so it isn't in ratings.
    ratings = [
        CardRating("Agent Phil Coulson", 70.3),
        CardRating("Lowsample Nobody", None),
    ]
    specs = build_label_specs(located, ratings, dpr=1.0)

    assert len(specs) == 2
    by_text = {s.text: (s.x, s.y, s.w, s.h) for s in specs}
    assert by_text["GIH 70.3"] == (100, 100, 120, 168)
    assert by_text["GIH N/A"] == (500, 100, 120, 168)


def test_build_label_specs_scales_with_dpr():
    located = [CardLocation("A", BBox(200, 400, 120, 160), 0.9)]
    ratings = [CardRating("A", 60.0)]
    specs = build_label_specs(located, ratings, dpr=2.0)
    assert (specs[0].x, specs[0].y, specs[0].w, specs[0].h) == (100, 200, 60, 80)


def test_expansion_from_log_path():
    assert expansion_from_log_path("C:/logs/User-replayMH3.txt") == "MH3"
    assert expansion_from_log_path("/var/x/Whatever-blb.txt") == "BLB"


def test_app_module_imports():
    # Importing the integration module pulls Qt + every subsystem; this guards
    # against import-time wiring errors even though the live loop needs Windows.
    import mtgo_overlay.app as app

    assert hasattr(app, "AppController")
    assert hasattr(app, "main")


def test_app_controller_constructs(qapp, tmp_path, monkeypatch):
    # Construction wires overlay/tracker/repo/signals (WSL-safe). We do NOT call
    # start() — that polls Win32, which only exists on Windows.
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    from mtgo_overlay.app import AppController

    controller = AppController(qapp)
    assert controller.overlay is not None
    assert controller.tracker is not None
    assert controller.repo is not None
    assert controller.log is None
