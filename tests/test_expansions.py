"""SupportedSets: filters fetch + disk-cache TTL, and the pure dropdown helpers."""

from __future__ import annotations

from mtgo_overlay.data.expansions import (
    SupportedSets,
    codes_newest_first,
    format_for,
    is_mtgo_draftable,
)
from mtgo_overlay.data.seventeenlands import SeventeenLandsError


SAMPLE_FILTERS = {
    "expansions": ["OLD", "NEW", "MID"],
    "start_dates": {
        "OLD": "2020-01-01T00:00:00Z",
        "MID": "2023-06-01T00:00:00Z",
        "NEW": "2025-02-01T00:00:00Z",
    },
    "formats_by_expansion": {
        "NEW": ["PremierDraft", "TradDraft", "QuickDraft"],
        "MID": ["QuickDraft", "PremierDraft"],
        "OLD": [],
    },
}


class _StubClient:
    def __init__(self, filters=None, error=None):
        self._filters = filters if filters is not None else {}
        self._error = error
        self.calls = 0

    def fetch_filters(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._filters


# --- pure helpers ----------------------------------------------------------

def test_codes_newest_first_orders_by_start_date():
    assert codes_newest_first(SAMPLE_FILTERS) == ["NEW", "MID", "OLD"]


def test_codes_newest_first_empty():
    assert codes_newest_first({}) == []


def test_codes_newest_first_drops_blank_pseudo_expansion():
    assert codes_newest_first({"expansions": ["", "X"], "start_dates": {}}) == ["X"]


def test_is_mtgo_draftable_keeps_real_set():
    assert is_mtgo_draftable("MSH", ["PremierDraft", "TradDraft", "Sealed"])


def test_is_mtgo_draftable_drops_alchemy_no_traddraft():
    assert not is_mtgo_draftable("Y26SOS", ["PremierDraft", "PickTwoDraft"])


def test_is_mtgo_draftable_drops_event_pseudo_sets():
    # Mixed-case / spaced names: the Arena cube/chaos/remix events.
    assert not is_mtgo_draftable("Cube", ["PremierDraft", "TradDraft"])
    assert not is_mtgo_draftable("Cube - Powered", ["PremierDraft", "TradDraft"])
    assert not is_mtgo_draftable("Chaos", ["PremierDraft", "TradDraft"])


def test_is_mtgo_draftable_drops_arena_only_remasters():
    assert not is_mtgo_draftable("HBG", ["PremierDraft", "TradDraft", "QuickDraft"])
    assert not is_mtgo_draftable("KLR", ["PremierDraft", "TradDraft"])


def test_codes_newest_first_mtgo_only_filters_and_orders():
    filters = {
        "expansions": ["MSH", "Y26SOS", "Cube", "HBG", "OLD"],
        "start_dates": {
            "MSH": "2026-06-23T00:00:00Z",
            "Y26SOS": "2026-05-19T00:00:00Z",
            "Cube": "2026-05-31T00:00:00Z",
            "HBG": "2022-07-07T00:00:00Z",
            "OLD": "2020-01-01T00:00:00Z",
        },
        "formats_by_expansion": {
            "MSH": ["PremierDraft", "TradDraft"],
            "Y26SOS": ["PremierDraft"],
            "Cube": ["PremierDraft", "TradDraft"],
            "HBG": ["PremierDraft", "TradDraft"],
            "OLD": ["PremierDraft", "TradDraft"],
        },
    }
    assert codes_newest_first(filters, mtgo_only=True) == ["MSH", "OLD"]


def test_format_for_prefers_supported_format():
    assert format_for("NEW", "TradDraft", SAMPLE_FILTERS) == "TradDraft"


def test_format_for_falls_back_to_first_format():
    # MID doesn't list TradDraft -> its first listed format.
    assert format_for("MID", "TradDraft", SAMPLE_FILTERS) == "QuickDraft"


def test_format_for_unknown_set_defaults_to_premier():
    assert format_for("ZZZ", "TradDraft", SAMPLE_FILTERS) == "PremierDraft"


# --- cache behavior --------------------------------------------------------

def test_ensure_caches_and_serves_within_ttl(tmp_path):
    clock = {"t": 1000.0}
    client = _StubClient(filters=SAMPLE_FILTERS)
    sets = SupportedSets(client, tmp_path, time_fn=lambda: clock["t"])

    assert sets.ensure() == SAMPLE_FILTERS
    assert client.calls == 1
    assert sets.ensure() == SAMPLE_FILTERS  # served from cache, no second fetch
    assert client.calls == 1


def test_ensure_refetches_past_ttl(tmp_path):
    clock = {"t": 1000.0}
    client = _StubClient(filters=SAMPLE_FILTERS)
    sets = SupportedSets(client, tmp_path, ttl_seconds=100, time_fn=lambda: clock["t"])

    sets.ensure()
    clock["t"] += 101
    sets.ensure()
    assert client.calls == 2


def test_ensure_error_no_cache_returns_empty(tmp_path):
    client = _StubClient(error=SeventeenLandsError("offline"))
    sets = SupportedSets(client, tmp_path)
    assert sets.ensure() == {}


def test_ensure_error_serves_stale_cache(tmp_path):
    clock = {"t": 1000.0}
    SupportedSets(
        _StubClient(filters=SAMPLE_FILTERS), tmp_path,
        ttl_seconds=100, time_fn=lambda: clock["t"],
    ).ensure()  # seed cache

    clock["t"] += 1000  # cache now stale
    offline = SupportedSets(
        _StubClient(error=SeventeenLandsError("offline")), tmp_path,
        ttl_seconds=100, time_fn=lambda: clock["t"],
    )
    assert offline.ensure() == SAMPLE_FILTERS
