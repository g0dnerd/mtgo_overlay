"""AppController pure helpers: label formatting, coord mapping, the join."""

from __future__ import annotations

from mtgo_overlay.app import (
    _set_icon,
    build_label_specs,
    expansion_from_log_path,
    map_capture_to_logical,
)
from mtgo_overlay.data.prices_repo import CardPrice
from mtgo_overlay.data.ratings_repo import CardRating
from mtgo_overlay.recognition.types import BBox, CardLocation


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
    by_wr = {s.gih_wr: (s.x, s.y, s.w, s.h) for s in specs}
    assert by_wr[70.3] == (100, 100, 120, 168)
    assert by_wr[None] == (500, 100, 120, 168)


def test_build_label_specs_colors_by_set_percentile():
    located = [
        CardLocation("Worst", BBox(0, 0, 100, 100), 0.9),
        CardLocation("Best", BBox(100, 0, 100, 100), 0.9),
        CardLocation("Unrated", BBox(200, 0, 100, 100), 0.9),
    ]
    ratings = [
        CardRating("Worst", 48.0),
        CardRating("Best", 64.0),
        CardRating("Unrated", None),
    ]
    distribution = sorted([48.0, 50.0, 52.0, 54.0, 56.0, 58.0, 60.0, 62.0, 64.0, 66.0])
    specs = build_label_specs(located, ratings, dpr=1.0, distribution=distribution)
    by_wr = {s.gih_wr: s.tier for s in specs}
    assert by_wr[48.0] < 0.15            # bottom of the set -> near 0 (red)
    assert by_wr[64.0] > 0.80            # top of the set -> near 1 (green)
    assert by_wr[None] is None           # unrated -> no tier (neutral pill)


def test_build_label_specs_joins_prices_by_printing_id_over_threshold():
    located = [
        CardLocation("Bomb", BBox(0, 0, 100, 140), 0.9, printing_id="p-bomb"),
        CardLocation("Chaff", BBox(100, 0, 100, 140), 0.9, printing_id="p-chaff"),
    ]
    ratings = [CardRating("Bomb", 60.0), CardRating("Chaff", 50.0)]
    prices = [CardPrice("p-bomb", 2.5), CardPrice("p-chaff", 0.3)]  # chaff < 1.0
    specs = build_label_specs(
        located, ratings, dpr=1.0, prices=prices,
        show_prices=True, price_min_tix=1.0,
    )
    by_wr = {s.gih_wr: s.tix for s in specs}
    assert by_wr[60.0] == 2.5    # priced: at/above threshold
    assert by_wr[50.0] is None   # below the 1.0 tix threshold -> no price pill


def test_build_label_specs_prices_off_leaves_tix_none():
    located = [CardLocation("Bomb", BBox(0, 0, 100, 140), 0.9, printing_id="p-bomb")]
    ratings = [CardRating("Bomb", 60.0)]
    prices = [CardPrice("p-bomb", 5.0)]
    specs = build_label_specs(
        located, ratings, dpr=1.0, prices=prices,
        show_prices=False, price_min_tix=1.0,
    )
    assert specs[0].tix is None  # toggle off -> never priced, even above threshold


def test_build_label_specs_null_tix_printing_shows_no_price():
    located = [CardLocation("Bomb", BBox(0, 0, 100, 140), 0.9, printing_id="p-bomb")]
    ratings = [CardRating("Bomb", 60.0)]
    prices = [CardPrice("p-bomb", None)]  # this printing has no tix
    specs = build_label_specs(
        located, ratings, dpr=1.0, prices=prices,
        show_prices=True, price_min_tix=1.0,
    )
    assert specs[0].tix is None


def test_build_label_specs_scales_with_dpr():
    located = [CardLocation("A", BBox(200, 400, 120, 160), 0.9)]
    ratings = [CardRating("A", 60.0)]
    specs = build_label_specs(located, ratings, dpr=2.0)
    assert (specs[0].x, specs[0].y, specs[0].w, specs[0].h) == (100, 200, 60, 80)


def test_expansion_from_log_path():
    assert expansion_from_log_path("C:/logs/User-replayMH3.txt") == "MH3"
    assert expansion_from_log_path("/var/x/Whatever-blb.txt") == "BLB"


def test_set_icon_uncached_returns_empty(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    from PySide6.QtGui import QColor

    assert _set_icon("ZZZ", QColor("white")).isNull()


def test_set_icon_renders_cached_svg(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    from PySide6.QtGui import QColor

    from mtgo_overlay.recognition import scryfall_art
    from mtgo_overlay.system import paths

    icon_path = scryfall_art._icon_path("XYZ", paths.scryfall_cache_dir())
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
        '<rect width="10" height="10" fill="#000"/></svg>'
    )
    icon = _set_icon("XYZ", QColor("white"))
    assert not icon.isNull()
    assert icon.availableSizes()


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


class _FakeLog:
    """Stand-in for draft.log_parser.Log with a scripted check_for_update."""

    def __init__(self, statuses, pack):
        self._statuses = list(statuses)
        self.picks: list[str] = []
        self.current_pack = list(pack)

    def check_for_update(self) -> str:
        return self._statuses.pop(0)


def _controller(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MTGO_OVERLAY_HOME", str(tmp_path))
    from mtgo_overlay.app import AppController, LabelSpec

    controller = AppController(qapp)
    controller.overlay.set_labels([LabelSpec(60.0, 0.5, 0, 0, 100, 140)])
    return controller


def test_pick_clears_labels_and_suppresses_recognition(qapp, tmp_path, monkeypatch):
    # After a pick the picked pack lingers in current_pack, so the controller must
    # not render labels until a new pack arrives — even if a refocus/resize tries
    # to re-recognize, or an in-flight worker reports back.
    controller = _controller(qapp, tmp_path, monkeypatch)
    controller.log = _FakeLog(["picked"], pack=["Agent Phil Coulson"])

    gen_before = controller._generation
    controller._on_log_modified("ignored")

    assert controller._awaiting_pack is True
    assert controller.overlay._labels == []  # cleared on pick

    # A refocus/resize during the gap routes through _dispatch_recognition; it must
    # not kick off a worker (generation stays put) despite current_pack being set.
    controller._dispatch_recognition()
    assert controller._generation == gen_before

    # An in-flight worker that finishes after the pick must not repaint stale labels.
    controller._on_labels(
        {"generation": gen_before, "located": [], "ratings": [], "rect": None}
    )
    assert controller.overlay._labels == []


def test_new_pack_clears_awaiting_flag(qapp, tmp_path, monkeypatch):
    controller = _controller(qapp, tmp_path, monkeypatch)
    controller._awaiting_pack = True
    controller._draft_prepared = True
    controller.log = _FakeLog(["new"], pack=["Some Card"])

    controller._on_log_modified("ignored")

    assert controller._awaiting_pack is False


def _cohort_actions(menu):
    return [a for a in menu.actions() if a.text().startswith("Win rates:")]


def test_cohort_picks_disabled_when_live_off(qapp, tmp_path, monkeypatch):
    # Live 17Lands off => the user's CSV is the source, which is a single cohort.
    controller = _controller(qapp, tmp_path, monkeypatch)
    controller.settings.use_live_17lands = False

    menu = controller._build_menu()
    actions = _cohort_actions(menu)
    assert len(actions) == 2
    assert all(not a.isEnabled() for a in actions)


def test_cohort_picks_enabled_with_live_and_no_draft(qapp, tmp_path, monkeypatch):
    # Live on, no active draft => live is the source, so the cohort split applies.
    controller = _controller(qapp, tmp_path, monkeypatch)
    controller.settings.use_live_17lands = True
    controller.expansion = ""

    menu = controller._build_menu()
    assert all(a.isEnabled() for a in _cohort_actions(menu))


def test_cohort_picks_disabled_when_embargoed(qapp, tmp_path, monkeypatch):
    # Live on but the active set is still under the new-set embargo (unknown start
    # date fails closed) => the CSV stands in, so the cohort split is moot.
    controller = _controller(qapp, tmp_path, monkeypatch)
    controller.settings.use_live_17lands = True
    controller.expansion = "FIN"
    controller._filters = {}

    menu = controller._build_menu()
    assert all(not a.isEnabled() for a in _cohort_actions(menu))
