"""goatbots.py — parse the zipped price feed. Hermetic (no net) + gated live."""

from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
import requests

from mtgo_overlay.data import goatbots


def _zip_bytes(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, payload)
    return buf.getvalue()


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return _FakeResp(self._content)


def test_fetch_prices_parses_bom_zip_and_filters_non_numeric():
    body = b"\xef\xbb\xbf" + json.dumps(
        {"90653": 8.45, "153888": 5.08, "bad": None}
    ).encode("utf-8")
    session = _FakeSession(_zip_bytes("price-history-2026-07-04.txt", body))

    prices = goatbots.fetch_prices(session=session)

    assert prices == {"90653": 8.45, "153888": 5.08}  # BOM stripped, null dropped
    assert session.calls == [goatbots.PRICES_URL]


def test_fetch_prices_raises_on_bad_zip():
    session = _FakeSession(b"not a zip")
    with pytest.raises(goatbots.GoatbotsError):
        goatbots.fetch_prices(session=session)


@pytest.mark.skipif(
    not os.environ.get("MTGO_OVERLAY_LIVE_GOATBOTS"),
    reason="set MTGO_OVERLAY_LIVE_GOATBOTS=1 to run the live Goatbots price fetch",
)
def test_live_fetch_prices_returns_real_prices():
    prices = goatbots.fetch_prices()
    assert len(prices) > 10000, "Goatbots feed should cover the whole MTGO catalog"
    assert prices.get("90653"), "Ragavan (mtgo_id 90653) should have a price"
    assert all(isinstance(v, float) for v in prices.values())
