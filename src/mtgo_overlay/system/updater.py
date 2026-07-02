"""Self-update check against GitHub Releases.

Import-safe everywhere: the version math and JSON parsing are pure and run under
WSL/Linux for the unit tests; only :func:`launch_installer` touches the OS shell
and is guarded on :data:`win32.IS_WINDOWS`.

The network seam mirrors :class:`data.ratings_repo.RatingsRepository`: callers may
inject a ``requests.Session`` so the fetch/download paths are testable with a fake.
Only GitHub's ``releases/latest`` endpoint is used, which excludes prereleases -
so ``-rc`` / ``-test`` tags never surface as an available update.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

from . import win32
from .. import __version__

REPO = "g0dnerd/mtgo_overlay"
ASSET_NAME = "MtgoOverlaySetup.exe"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{REPO}/releases"

# GitHub requires a User-Agent; derive it from the running version rather than
# reusing settings.user_agent (which is pinned for the 17Lands endpoint).
USER_AGENT = f"MtgoOverlay/{__version__} (+https://github.com/{REPO})"

_TIMEOUT = 30.0


def parse_version(s: str) -> tuple[int, ...]:
    """Parse a ``vX.Y.Z`` tag into a comparable int tuple.

    Strips a leading ``v``, drops any ``-suffix`` prerelease part, and splits the
    core on ``.``. Returns ``()`` if any component is non-numeric, so a malformed
    tag sorts below every real version (and :func:`is_newer` treats it as "no
    update").
    """
    if not s:
        return ()
    core = s.strip().lstrip("vV").split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return ()


def is_newer(remote: str, current: str) -> bool:
    """Whether ``remote`` is a strictly newer version than ``current``."""
    r = parse_version(remote)
    if not r:
        return False
    return r > parse_version(current)


@dataclass
class ReleaseInfo:
    version: str
    tag: str
    html_url: str
    asset_name: str
    download_url: str
    body: str


def _pick_asset(assets: list[dict]) -> dict | None:
    """The installer asset: prefer the exact :data:`ASSET_NAME`, else the first
    ``.exe`` asset. ``None`` if the release ships no exe."""
    for asset in assets:
        if asset.get("name") == ASSET_NAME:
            return asset
    for asset in assets:
        name = asset.get("name", "")
        if name.lower().endswith(".exe"):
            return asset
    return None


def fetch_latest_release(session: requests.Session | None = None) -> ReleaseInfo | None:
    """GET the ``releases/latest`` endpoint and parse it into a :class:`ReleaseInfo`.

    Returns ``None`` on any network / parse error or if the release has no usable
    installer asset - the caller logs and treats it as "no update available".
    """
    sess = session or requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    try:
        resp = sess.get(LATEST_URL, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    tag = data.get("tag_name") or ""
    asset = _pick_asset(data.get("assets") or [])
    if not tag or asset is None:
        return None
    return ReleaseInfo(
        version=tag.lstrip("vV"),
        tag=tag,
        html_url=data.get("html_url") or RELEASES_URL,
        asset_name=asset.get("name", ASSET_NAME),
        download_url=asset.get("browser_download_url", ""),
        body=data.get("body") or "",
    )


def download_installer(
    url: str, dest: str, session: requests.Session | None = None
) -> None:
    """Stream the installer at ``url`` to ``dest`` in chunks. Raises on failure."""
    sess = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    with sess.get(url, headers=headers, timeout=_TIMEOUT, stream=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)


def launch_installer(path: str) -> None:
    """Launch the downloaded installer, detached, so it outlives the quitting app.

    Windows-only: ``os.startfile`` hands the exe to the shell, which keeps running
    after this process exits (letting Inno replace the now-unlocked files).
    """
    if not win32.IS_WINDOWS:
        raise RuntimeError("launch_installer is only available on Windows")
    os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - Windows only
