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


def test_image_url_prefers_png_then_falls_back():
    assert scryfall_art._image_url({"image_uris": {"png": "p", "large": "l"}}) == "p"
    assert scryfall_art._image_url({"image_uris": {"large": "l"}}) == "l"
    faces = {"card_faces": [{"image_uris": {"png": "front"}}]}
    assert scryfall_art._image_url(faces) == "front"
    assert scryfall_art._image_url({}) is None


def test_booster_artwork_ids_queries_and_caches(tmp_path, monkeypatch):
    pages = [
        FakeResp({
            "has_more": True,
            "next_page": "http://api/next",
            "data": [{"id": "a1", "name": "X", "image_uris": {"png": "u1"}}],
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
    assert calls["n"] == 2  # paginated

    # Now cached: a second call must not hit the network.
    monkeypatch.setattr(scryfall_art, "_http_get", lambda *a, **k: pytest.fail("network"))
    again = scryfall_art.booster_artwork_ids("MSH", "X", cache_dir=tmp_path)
    assert again == refs
    cache = json.loads((tmp_path / "MSH_variants.json").read_text())
    assert cache["X"][0]["scryfall_id"] == "a1"


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


def test_ensure_set_artwork_dedups_and_fetches(tmp_path, monkeypatch):
    enum_calls: list[str] = []
    fetched: list[str] = []

    def fake_enum(exp, name, **kw):
        enum_calls.append(name)
        return [ArtRef(f"{name}-1", "u", name)]

    monkeypatch.setattr(scryfall_art, "booster_artwork_ids", fake_enum)
    monkeypatch.setattr(
        scryfall_art, "fetch_artwork", lambda ref, cache_dir: fetched.append(ref.scryfall_id)
    )

    scryfall_art.ensure_set_artwork("MSH", ["A", "B", "A"], cache_dir=tmp_path)
    assert enum_calls == ["A", "B"]  # deduped
    assert fetched == ["A-1", "B-1"]
