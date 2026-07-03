"""scryfall_art.py — enumeration, caching, rate limiting. All hermetic (no net)."""

from __future__ import annotations

import json
import time

import pytest
import requests

from mtgo_overlay.recognition import scryfall_art
from mtgo_overlay.recognition.scryfall_art import ArtRef, _RateLimiter


class FakeResp:
    def __init__(self, json_data=None, content=b""):
        self._json = json_data
        self.content = content

    def json(self):
        return self._json


def test_rate_limiter_enforces_interval():
    limiter = _RateLimiter(0.05)
    limiter.wait()  # first call: no prior request, no sleep
    start = time.monotonic()
    limiter.wait()  # must wait ~0.05s
    assert time.monotonic() - start >= 0.045


def test_limiter_for_routes_search_vs_other():
    assert (
        scryfall_art._limiter_for("https://api.scryfall.com/cards/search?q=x")
        is scryfall_art._search_limiter
    )
    assert (
        scryfall_art._limiter_for("https://cards.scryfall.io/png/front.png")
        is scryfall_art._default_limiter
    )


def test_http_get_backs_off_and_retries_on_429(monkeypatch):
    class R:
        def __init__(self, status, headers=None):
            self.status_code = status
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        return R(429, {"Retry-After": "2"}) if calls["n"] == 1 else R(200)

    sleeps: list[float] = []
    monkeypatch.setattr(scryfall_art._session, "get", fake_get)
    monkeypatch.setattr(scryfall_art._default_limiter, "wait", lambda: None)
    monkeypatch.setattr(scryfall_art.time, "sleep", lambda s: sleeps.append(s))

    resp = scryfall_art._http_get("https://api.scryfall.com/x")
    assert resp.status_code == 200
    assert calls["n"] == 2  # retried after the 429
    assert sleeps == [2.0]  # honoured Retry-After


def test_http_get_raises_after_exhausting_429_retries(monkeypatch):
    class R:
        status_code = 429
        headers = {"Retry-After": "1"}

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    monkeypatch.setattr(scryfall_art._session, "get", lambda *a, **k: R())
    monkeypatch.setattr(scryfall_art._default_limiter, "wait", lambda: None)
    monkeypatch.setattr(scryfall_art.time, "sleep", lambda s: None)

    with pytest.raises(requests.HTTPError):
        scryfall_art._http_get("https://api.scryfall.com/x")


def test_image_url_prefers_png_then_falls_back():
    assert scryfall_art._image_url({"image_uris": {"png": "p", "large": "l"}}) == "p"
    assert scryfall_art._image_url({"image_uris": {"large": "l"}}) == "l"
    faces = {"card_faces": [{"image_uris": {"png": "front"}}]}
    assert scryfall_art._image_url(faces) == "front"
    assert scryfall_art._image_url({}) is None


def test_booster_artwork_ids_fetches_whole_set_once_and_caches(tmp_path, monkeypatch):
    pages = [
        FakeResp({
            "has_more": True,
            "next_page": "http://api/next",
            "data": [
                {"id": "a1", "name": "X", "image_uris": {"png": "u1"}},
                {"id": "y1", "name": "Y", "image_uris": {"png": "uy"}},
            ],
        }),
        FakeResp({
            "has_more": False,
            "data": [{"id": "a2", "name": "X", "image_uris": {"png": "u2"}}],
        }),
    ]
    calls = {"n": 0}

    def fake_get(url, params=None):
        resp = pages[calls["n"]]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(scryfall_art, "_http_get", fake_get)

    refs = scryfall_art.booster_artwork_ids("MSH", "X", cache_dir=tmp_path)
    assert refs == [ArtRef("a1", "u1", "X"), ArtRef("a2", "u2", "X")]
    assert calls["n"] == 2  # one paginated set fetch, not one search per name

    # A different name from the same set is served from the same warmed index.
    monkeypatch.setattr(scryfall_art, "_http_get", lambda *a, **k: pytest.fail("network"))
    assert scryfall_art.booster_artwork_ids("MSH", "Y", cache_dir=tmp_path) == [
        ArtRef("y1", "uy", "Y")
    ]
    cache = json.loads((tmp_path / "MSH_variants.json").read_text())
    assert cache["complete"] is True
    assert cache["cards"]["X"][0]["scryfall_id"] == "a1"


def test_booster_artwork_ids_matches_mdfc_front_face(tmp_path, monkeypatch):
    page = FakeResp({
        "has_more": False,
        "data": [{"id": "p1", "name": "Front // Back", "image_uris": {"png": "uf"}}],
    })
    monkeypatch.setattr(scryfall_art, "_http_get", lambda *a, **k: page)
    # MTGO's log may give only the front face; it must still resolve.
    assert scryfall_art.booster_artwork_ids("MSH", "Front", cache_dir=tmp_path) == [
        ArtRef("p1", "uf", "Front // Back")
    ]


def test_enumerate_set_cards_paginates_and_dedups(tmp_path, monkeypatch):
    pages = [
        FakeResp({
            "has_more": True,
            "next_page": "http://api/next",
            "data": [
                {"id": "1", "name": "Alpha", "image_uris": {"png": "a"}},
                {"id": "2", "name": "Beta", "image_uris": {"png": "b"}},
            ],
        }),
        FakeResp({
            "has_more": False,
            "data": [
                {"id": "3", "name": "Beta", "image_uris": {"png": "b2"}},
                {"id": "4", "name": "Gamma", "image_uris": {"png": "g"}},
            ],
        }),
    ]
    calls = {"n": 0}

    def fake_get(url, params=None):
        resp = pages[calls["n"]]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(scryfall_art, "_http_get", fake_get)
    names = scryfall_art.enumerate_set_cards("MSH", cache_dir=tmp_path)
    assert names == ["Alpha", "Beta", "Gamma"]  # order preserved, deduped by name
    assert calls["n"] == 2  # paginated


def test_enumerate_set_cards_404_returns_empty(tmp_path, monkeypatch):
    def fake_get(url, params=None):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(scryfall_art, "_http_get", fake_get)
    assert scryfall_art.enumerate_set_cards("ZZZ", cache_dir=tmp_path) == []


def test_booster_artwork_ids_404_returns_empty(tmp_path, monkeypatch):
    def fake_get(url, params=None):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(scryfall_art, "_http_get", fake_get)
    assert scryfall_art.booster_artwork_ids("MSH", "Nope", cache_dir=tmp_path) == []


def test_fetch_artwork_cache_first(tmp_path, monkeypatch):
    ref = ArtRef("id9", "http://x/i.png", "N")
    cached = tmp_path / "id9.png"
    cached.write_bytes(b"already")
    monkeypatch.setattr(scryfall_art, "_http_get", lambda *a, **k: pytest.fail("network"))
    assert scryfall_art.fetch_artwork(ref, tmp_path) == cached


def test_fetch_artwork_downloads_and_caches(tmp_path, monkeypatch):
    ref = ArtRef("id7", "http://x/i.png", "N")
    monkeypatch.setattr(scryfall_art, "_http_get", lambda *a, **k: FakeResp(content=b"PNG"))
    out = scryfall_art.fetch_artwork(ref, tmp_path)
    assert out == tmp_path / "id7.png"
    assert out.read_bytes() == b"PNG"


def test_fetch_set_tix_maps_printings_and_parses(monkeypatch):
    pages = [
        FakeResp({
            "has_more": True,
            "next_page": "http://api/next",
            "data": [
                {"id": "a1", "name": "X", "prices": {"tix": "2.11"}},
                {"id": "a2", "name": "X", "prices": {"tix": "0.75"}},
            ],
        }),
        FakeResp({
            "has_more": False,
            "data": [
                {"id": "a3", "name": "Y", "prices": {"tix": None}},
                {"id": "a4", "name": "Z", "prices": {}},  # no tix key
            ],
        }),
    ]
    calls = {"n": 0}

    def fake_get(url, params=None):
        resp = pages[calls["n"]]
        calls["n"] += 1
        return resp

    monkeypatch.setattr(scryfall_art, "_http_get", fake_get)
    tix = scryfall_art.fetch_set_tix("STX")
    assert tix == {"a1": 2.11, "a2": 0.75, "a3": None, "a4": None}
    assert calls["n"] == 2  # one paginated set search, shared with the art index


def test_fetch_set_tix_unknown_set_returns_empty(monkeypatch):
    def fake_get(url, params=None):
        resp = requests.Response()
        resp.status_code = 404
        raise requests.HTTPError(response=resp)

    monkeypatch.setattr(scryfall_art, "_http_get", fake_get)
    assert scryfall_art.fetch_set_tix("ZZZ") == {}


def test_ensure_set_artwork_fetches_set_once_and_dedups(tmp_path, monkeypatch):
    index_calls: list[str] = []
    fetched: list[str] = []

    def fake_index(exp, **kw):
        index_calls.append(exp)
        return {"A": [ArtRef("A-1", "u", "A")], "B": [ArtRef("B-1", "u", "B")]}

    monkeypatch.setattr(scryfall_art, "set_artwork_index", fake_index)
    monkeypatch.setattr(
        scryfall_art, "fetch_artwork", lambda ref, cache_dir: fetched.append(ref.scryfall_id)
    )

    scryfall_art.ensure_set_artwork("MSH", ["A", "B", "A"], cache_dir=tmp_path)
    assert index_calls == ["MSH"]  # one set fetch warms every name
    assert fetched == ["A-1", "B-1"]  # deduped, images downloaded per name
