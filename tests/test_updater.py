"""Updater: version parsing/compare, release-JSON parsing + asset selection.

Hermetic; the single live test hits the real GitHub endpoint and is gated behind
``MTGO_OVERLAY_LIVE_GITHUB=1`` (unauthenticated GitHub is 60 req/hr/IP).
"""

from __future__ import annotations

import os

import pytest

from mtgo_overlay.system import updater
from mtgo_overlay.system.updater import (
    ReleaseInfo,
    fetch_latest_release,
    is_newer,
    parse_version,
)


# --- version math ----------------------------------------------------------

def test_parse_version_strips_v_and_suffix():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("v0.2.0-rc1") == (0, 2, 0)
    assert parse_version("v1.0") == (1, 0)


def test_parse_version_malformed_is_empty():
    assert parse_version("") == ()
    assert parse_version("nightly") == ()
    assert parse_version("v1.x.3") == ()


def test_is_newer():
    assert is_newer("0.3.0", "0.2.0")
    assert is_newer("v1.0.0", "0.9.9")
    assert is_newer("0.2.1", "0.2.0")
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.1.0", "0.2.0")
    # A malformed remote never reads as an update.
    assert not is_newer("garbage", "0.2.0")


# --- release fetch / asset selection --------------------------------------

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

    def get(self, url, headers=None, timeout=None):
        self.calls.append({"url": url, "headers": headers})
        return self._response


def _release_payload(assets, tag="v0.3.0"):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{updater.REPO}/releases/tag/{tag}",
        "body": "Release notes here.",
        "assets": assets,
    }


def test_fetch_latest_prefers_named_asset():
    payload = _release_payload(
        [
            {"name": "other.exe", "browser_download_url": "https://x/other.exe"},
            {
                "name": updater.ASSET_NAME,
                "browser_download_url": f"https://x/{updater.ASSET_NAME}",
            },
        ]
    )
    session = _FakeSession(_FakeResponse(payload))
    info = fetch_latest_release(session=session)
    assert isinstance(info, ReleaseInfo)
    assert info.version == "0.3.0"
    assert info.tag == "v0.3.0"
    assert info.asset_name == updater.ASSET_NAME
    assert info.download_url == f"https://x/{updater.ASSET_NAME}"
    assert "MtgoOverlay" in session.calls[0]["headers"]["User-Agent"]


def test_fetch_latest_falls_back_to_first_exe():
    payload = _release_payload(
        [
            {"name": "notes.txt", "browser_download_url": "https://x/notes.txt"},
            {"name": "Installer.exe", "browser_download_url": "https://x/Installer.exe"},
        ]
    )
    info = fetch_latest_release(session=_FakeSession(_FakeResponse(payload)))
    assert info is not None
    assert info.asset_name == "Installer.exe"


def test_fetch_latest_none_without_exe_asset():
    payload = _release_payload(
        [{"name": "notes.txt", "browser_download_url": "https://x/notes.txt"}]
    )
    assert fetch_latest_release(session=_FakeSession(_FakeResponse(payload))) is None


def test_fetch_latest_none_on_http_error():
    session = _FakeSession(_FakeResponse({}, status_ok=False))
    assert fetch_latest_release(session=session) is None


def test_fetch_latest_none_on_malformed_json():
    assert fetch_latest_release(session=_FakeSession(_FakeResponse(["not", "a", "dict"]))) is None
    assert fetch_latest_release(session=_FakeSession(_FakeResponse({"assets": []}))) is None


# --- live (opt-in) ---------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("MTGO_OVERLAY_LIVE_GITHUB"),
    reason="set MTGO_OVERLAY_LIVE_GITHUB=1 to hit the real GitHub endpoint",
)
def test_live_fetch_latest_release():
    info = fetch_latest_release()
    assert info is not None
    assert parse_version(info.tag) != ()
    assert info.download_url
