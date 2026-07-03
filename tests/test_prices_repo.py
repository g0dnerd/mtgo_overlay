"""prices_repo.py — printing-id keyed price cache: TTL, atomic write, lookup."""

from __future__ import annotations

import json
import os

import pytest

from mtgo_overlay.data.prices_repo import CardPrice, PricesRepository
from mtgo_overlay.recognition import scryfall_art


class _StubFetcher:
    """Records calls and returns a scripted ``{printing_id: tix}`` map."""

    def __init__(self, prices=None, error=None):
        self._prices = prices if prices is not None else {}
        self._error = error
        self.calls: list[str] = []

    def __call__(self, expansion):
        self.calls.append(expansion)
        if self._error:
            raise self._error
        return self._prices


def _repo(tmp_path, **kw):
    return PricesRepository(tmp_path, **kw)


def test_ensure_fetches_and_writes_atomically(tmp_path):
    fetcher = _StubFetcher({"a1": 2.11, "a2": 0.75, "a3": None})
    repo = _repo(tmp_path, client=fetcher, time_fn=lambda: 1000.0)

    path = repo.ensure("stx")
    assert fetcher.calls == ["stx"]
    payload = json.loads(path.read_text())
    assert payload["expansion"] == "STX"
    assert payload["fetched_at"] == 1000.0
    assert payload["prices"] == {"a1": 2.11, "a2": 0.75, "a3": None}
    # No leftover temp file from the atomic replace.
    assert not list(tmp_path.glob("*.tmp"))


def test_ensure_uses_fresh_cache_without_refetch(tmp_path):
    clock = {"t": 1000.0}
    fetcher = _StubFetcher({"a1": 1.0})
    repo = _repo(tmp_path, client=fetcher, time_fn=lambda: clock["t"])
    repo.ensure("stx")

    clock["t"] += 23 * 3600  # still inside the 24h TTL
    repo.ensure("stx")
    assert fetcher.calls == ["stx"]  # served from cache, no second fetch

    clock["t"] += 2 * 3600  # now 25h old -> stale
    repo.ensure("stx")
    assert fetcher.calls == ["stx", "stx"]


def test_is_fresh_tracks_ttl(tmp_path):
    clock = {"t": 1000.0}
    repo = _repo(tmp_path, client=_StubFetcher({"a1": 1.0}), time_fn=lambda: clock["t"])
    repo.ensure("stx")
    assert repo.is_fresh("stx")
    clock["t"] += 25 * 3600
    assert not repo.is_fresh("stx")


def test_ensure_all_null_prices_handled(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher({"a1": None, "a2": None}))
    path = repo.ensure("stx")
    assert json.loads(path.read_text())["prices"] == {"a1": None, "a2": None}
    out = repo.lookup("stx", ["a1", "a2"])
    assert out == [CardPrice("a1", None), CardPrice("a2", None)]


def test_lookup_by_printing_id_preserves_order_and_misses(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher({"a1": 2.11, "a2": 0.75}))
    repo.ensure("stx")
    out = repo.lookup("stx", ["a2", "a1", "unknown"])
    assert out == [
        CardPrice("a2", 0.75),
        CardPrice("a1", 2.11),
        CardPrice("unknown", None),  # id not in the set -> None, not a crash
    ]


def test_lookup_without_cache_returns_none(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher({}))
    assert repo.lookup("stx", ["a1"]) == [CardPrice("a1", None)]


def test_ensure_keeps_stale_cache_on_fetch_failure(tmp_path):
    clock = {"t": 1000.0}
    good = _StubFetcher({"a1": 3.0})
    repo = _repo(tmp_path, client=good, time_fn=lambda: clock["t"])
    repo.ensure("stx")  # seed
    clock["t"] += 100 * 3600  # go stale

    repo._client = _StubFetcher(error=RuntimeError("offline"))
    path = repo.ensure("stx")  # must not raise; retains the stale cache
    assert json.loads(path.read_text())["prices"] == {"a1": 3.0}


def test_ensure_raises_when_no_cache_and_fetch_fails(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher(error=RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        repo.ensure("stx")


def test_parse_tix():
    assert scryfall_art._parse_tix("2.11") == 2.11
    assert scryfall_art._parse_tix(None) is None
    assert scryfall_art._parse_tix("") is None
    assert scryfall_art._parse_tix("junk") is None


@pytest.mark.skipif(
    not os.environ.get("MTGO_OVERLAY_LIVE_SCRYFALL"),
    reason="set MTGO_OVERLAY_LIVE_SCRYFALL=1 to run the live Scryfall price fetch",
)
def test_live_fetch_set_tix_returns_real_prices():
    # Strixhaven is a stable, released set with plenty of priced printings.
    tix = scryfall_art.fetch_set_tix("stx")
    assert tix, "no printings returned for STX"
    assert any(v is not None for v in tix.values()), "every printing tix was null"
