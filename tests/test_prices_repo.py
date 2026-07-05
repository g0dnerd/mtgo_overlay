"""prices_repo.py — global Goatbots price cache: TTL, atomic write, lookup."""

from __future__ import annotations

import json

import pytest

from mtgo_overlay.data.prices_repo import CardPrice, PricesRepository


class _StubFetcher:
    """Records calls and returns a scripted ``{mtgo_id: price}`` map."""

    def __init__(self, prices=None, error=None):
        self._prices = prices if prices is not None else {}
        self._error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self._error:
            raise self._error
        return self._prices


def _repo(tmp_path, **kw):
    return PricesRepository(tmp_path, **kw)


def test_ensure_fetches_and_writes_atomically(tmp_path):
    fetcher = _StubFetcher({"90653": 8.45, "153888": 5.08})
    repo = _repo(tmp_path, client=fetcher, time_fn=lambda: 1000.0)

    path = repo.ensure()
    assert fetcher.calls == 1
    payload = json.loads(path.read_text())
    assert payload["fetched_at"] == 1000.0
    assert payload["prices"] == {"90653": 8.45, "153888": 5.08}
    assert not list(tmp_path.glob("*.tmp"))  # atomic replace left no temp file


def test_ensure_uses_fresh_cache_without_refetch(tmp_path):
    clock = {"t": 1000.0}
    fetcher = _StubFetcher({"90653": 8.45})
    repo = _repo(tmp_path, client=fetcher, time_fn=lambda: clock["t"])
    repo.ensure()

    clock["t"] += 5 * 3600  # inside the 6h TTL
    repo.ensure()
    assert fetcher.calls == 1  # served from cache

    clock["t"] += 2 * 3600  # now 7h old -> stale
    repo.ensure()
    assert fetcher.calls == 2


def test_is_fresh_tracks_6h_ttl(tmp_path):
    clock = {"t": 1000.0}
    repo = _repo(tmp_path, client=_StubFetcher({"1": 1.0}), time_fn=lambda: clock["t"])
    repo.ensure()
    assert repo.is_fresh()
    clock["t"] += 7 * 3600
    assert not repo.is_fresh()


def test_price_for_by_mtgo_id(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher({"90653": 8.45}))
    repo.ensure()
    assert repo.price_for(90653) == 8.45  # int
    assert repo.price_for("90653") == 8.45  # str
    assert repo.price_for(999999) is None  # unknown id
    assert repo.price_for(None) is None  # printing with no mtgo_id


def test_lookup_joins_pairs_back_by_scryfall_id(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher({"90653": 8.45}))
    repo.ensure()
    out = repo.lookup([("scry-rag", 90653), ("scry-x", 111), ("scry-paper", None)])
    assert out == [
        CardPrice("scry-rag", 8.45),
        CardPrice("scry-x", None),  # mtgo id not in the feed
        CardPrice("scry-paper", None),  # no mtgo id at all
    ]


def test_lookup_without_cache_returns_none(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher({}))
    assert repo.lookup([("scry-x", 1)]) == [CardPrice("scry-x", None)]


def test_ensure_keeps_stale_cache_on_fetch_failure(tmp_path):
    clock = {"t": 1000.0}
    repo = _repo(tmp_path, client=_StubFetcher({"90653": 8.45}), time_fn=lambda: clock["t"])
    repo.ensure()  # seed
    clock["t"] += 100 * 3600  # go stale

    repo._client = _StubFetcher(error=RuntimeError("offline"))
    path = repo.ensure()  # must not raise; retains the stale cache
    assert json.loads(path.read_text())["prices"] == {"90653": 8.45}
    # And a fresh instance still reads the retained prices.
    assert _repo(tmp_path).price_for(90653) == 8.45


def test_ensure_raises_when_no_cache_and_fetch_fails(tmp_path):
    repo = _repo(tmp_path, client=_StubFetcher(error=RuntimeError("offline")))
    with pytest.raises(RuntimeError):
        repo.ensure()


def test_ensure_reloads_in_memory_cache_after_refetch(tmp_path):
    clock = {"t": 1000.0}
    repo = _repo(tmp_path, client=_StubFetcher({"1": 1.0}), time_fn=lambda: clock["t"])
    repo.ensure()
    assert repo.price_for(1) == 1.0  # populates the in-memory map

    clock["t"] += 7 * 3600  # stale -> refetch with new prices
    repo._client = _StubFetcher({"1": 2.5})
    repo.ensure()
    assert repo.price_for(1) == 2.5  # in-memory map reflects the refetch
