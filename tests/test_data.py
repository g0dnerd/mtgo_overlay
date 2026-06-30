"""Data layer: CSV parsing, GIH% mapping, 17lands client, repo TTL + fallback."""

from __future__ import annotations

import json

import pytest

from mtgo_overlay.data.ratings_repo import (
    RatingsError,
    RatingsRepository,
    parse_17lands_csv,
)
from mtgo_overlay.data.seventeenlands import (
    SeventeenLandsClient,
    SeventeenLandsError,
)
from mtgo_overlay.data.sets import expansion_from_log_code, is_basic_land


# --- sets ------------------------------------------------------------------

def test_expansion_from_log_code():
    assert expansion_from_log_code("mh3") == "MH3"
    assert expansion_from_log_code(" Blb ") == "BLB"


def test_is_basic_land():
    assert is_basic_land("Island")
    assert not is_basic_land("Agent Phil Coulson")


# --- CSV parsing -----------------------------------------------------------

def test_parse_csv(fixtures_dir):
    ratings = parse_17lands_csv(fixtures_dir / "ratings" / "sample_card_ratings.csv")
    assert ratings["The Super Hero Civil War"] == 75.7
    assert ratings["Leader, Super-Genius"] == 72.6
    # Low-sample card with an empty GIH WR cell -> None, not a crash.
    assert ratings["Lowsample Nobody"] is None


# --- 17lands client --------------------------------------------------------

def test_gih_win_rate_mapping(fixtures_dir):
    data = json.loads((fixtures_dir / "ratings" / "sample_17lands.json").read_text())
    mapping = SeventeenLandsClient.to_ratings_map(data)
    assert mapping["The Super Hero Civil War"] == 75.7
    assert mapping["Lowsample Nobody"] is None


class _FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._ok = status_ok

    def raise_for_status(self):
        if not self._ok:
            import requests

            raise requests.HTTPError("boom")

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._response


def test_client_fetch_builds_request_and_parses(fixtures_dir):
    data = json.loads((fixtures_dir / "ratings" / "sample_17lands.json").read_text())
    session = _FakeSession(_FakeResponse(data))
    client = SeventeenLandsClient("MtgoOverlay/test (+contact)", session=session)

    out = client.fetch_ratings("mh3", "PremierDraft")

    assert out == data
    call = session.calls[0]
    assert call["params"]["expansion"] == "MH3"  # uppercased
    assert call["params"]["format"] == "PremierDraft"
    assert "MtgoOverlay" in call["headers"]["User-Agent"]


def test_client_rejects_non_array():
    session = _FakeSession(_FakeResponse({"not": "an array"}))
    client = SeventeenLandsClient("ua", session=session)
    with pytest.raises(SeventeenLandsError):
        client.fetch_ratings("mh3", "PremierDraft")


# --- repository ------------------------------------------------------------

class _StubClient:
    """Minimal stand-in matching what RatingsRepository calls."""

    def __init__(self, data=None, error=None):
        self._data = data or []
        self._error = error

    def fetch_ratings(self, expansion, fmt, **_):
        if self._error:
            raise self._error
        return self._data


CSV_NAMES = ["Agent Phil Coulson", "Island", "Leader, Super-Genius"]


def _repo(tmp_path, **kw):
    return RatingsRepository(tmp_path, **kw)


def test_repo_csv_first(tmp_path, fixtures_dir):
    repo = _repo(tmp_path)
    csv = fixtures_dir / "ratings" / "sample_card_ratings.csv"
    path = repo.ensure("mh3", "PremierDraft", use_live=False, csv_path=csv)

    payload = json.loads(path.read_text())
    assert payload["source"] == "csv"
    assert isinstance(payload["fetched_at"], (int, float))

    out = repo.lookup("mh3", "PremierDraft", CSV_NAMES)
    by_name = {r.name: r.gih_wr for r in out}
    assert "Island" not in by_name  # basics filtered in lookup
    assert by_name["Agent Phil Coulson"] == 70.3
    assert by_name["Leader, Super-Genius"] == 72.6


def test_repo_ttl_fresh_then_stale(tmp_path, fixtures_dir):
    clock = {"t": 1000.0}
    repo = _repo(tmp_path, time_fn=lambda: clock["t"])
    csv = fixtures_dir / "ratings" / "sample_card_ratings.csv"
    repo.ensure("mh3", "PremierDraft", use_live=False, csv_path=csv)

    assert repo.is_fresh("mh3", "PremierDraft")
    clock["t"] += 23 * 3600
    assert repo.is_fresh("mh3", "PremierDraft")
    clock["t"] += 2 * 3600  # now 25h old
    assert not repo.is_fresh("mh3", "PremierDraft")


def test_repo_live_success(tmp_path, fixtures_dir):
    data = json.loads((fixtures_dir / "ratings" / "sample_17lands.json").read_text())
    repo = _repo(tmp_path, client=_StubClient(data=data))
    path = repo.ensure("mh3", "PremierDraft", use_live=True)
    payload = json.loads(path.read_text())
    assert payload["source"] == "17lands"
    assert payload["ratings"]["The Super Hero Civil War"] == 75.7


def test_repo_live_failure_falls_back_to_csv(tmp_path, fixtures_dir):
    repo = _repo(tmp_path, client=_StubClient(error=SeventeenLandsError("offline")))
    csv = fixtures_dir / "ratings" / "sample_card_ratings.csv"
    path = repo.ensure("mh3", "PremierDraft", use_live=True, csv_path=csv)
    assert json.loads(path.read_text())["source"] == "csv"


def test_repo_keeps_stale_when_refresh_fails(tmp_path, fixtures_dir):
    clock = {"t": 1000.0}
    csv = fixtures_dir / "ratings" / "sample_card_ratings.csv"
    repo = _repo(tmp_path, client=_StubClient(error=SeventeenLandsError("offline")),
                 time_fn=lambda: clock["t"])
    repo.ensure("mh3", "PremierDraft", use_live=False, csv_path=csv)  # seed cache
    clock["t"] += 100 * 3600  # go stale

    # No CSV this time, live fails -> stale cache retained, no raise.
    path = repo.ensure("mh3", "PremierDraft", use_live=True, csv_path=None)
    assert path.exists()


def test_repo_raises_when_no_source(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RatingsError):
        repo.ensure("mh3", "PremierDraft", use_live=False, csv_path=None)
