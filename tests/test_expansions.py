"""SupportedSets: filters fetch + disk-cache TTL, and the pure dropdown helpers."""

from __future__ import annotations

from mtgo_overlay.data.expansions import (
    SupportedSets,
    codes_newest_first,
    format_for,
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
