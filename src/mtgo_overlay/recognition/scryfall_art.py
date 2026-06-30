"""Scryfall artwork enumeration + caching.

Ported from the old ``crawler/fetch.py``: the cache-first image fetch, the 10
req/s rate limit, and the ``{name: [variants]}`` model are reused. The heavyweight
``bulk_data.json`` + hand-authored ``information.json`` enumeration is replaced by
a direct Scryfall search (``set:<exp> !"name" unique=prints``), so it runs without
curated per-set data and warms the cache per draft.

Scryfall asks for a descriptive User-Agent and an Accept header, and enforces
per-endpoint hard rate limits: the ``/cards/*`` search-family endpoints are
2/second (500 ms), every other API method is 10/second (100 ms), and the
``*.scryfall.io`` image origins are unlimited. Requests are routed to the right
limiter by URL, HTTP 429s are honoured with backoff, and results are cached so
re-runs (and the recognition hot path) stay offline.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests

from ..system import paths
from ..system.logging_setup import get_logger

_log = get_logger("scryfall")

SCRYFALL_API = "https://api.scryfall.com"
# Scryfall's per-endpoint hard limits. The search family (/cards/search,
# /cards/named, /cards/random, /cards/collection) is 2/s; every other API method
# is 10/s. The *.scryfall.io image origins are unlimited but we throttle them too.
SEARCH_REQUEST_INTERVAL = 0.5
DEFAULT_REQUEST_INTERVAL = 0.1
MIN_REQUEST_INTERVAL = DEFAULT_REQUEST_INTERVAL  # back-compat alias
# A 429 limits access for ~30s; ignoring it risks a ban, so we back off and retry.
RATE_LIMIT_COOLDOWN = 30.0
MAX_RETRIES = 3
_SEARCH_PATHS = ("/cards/search", "/cards/named", "/cards/random", "/cards/collection")
USER_AGENT = "MtgoOverlay/0.2 (https://github.com/; MTGO draft overlay; personal use)"


@dataclass(frozen=True)
class ArtRef:
    """One booster-eligible artwork of a card."""

    scryfall_id: str
    image_url: str
    name: str


class _RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self._min = min_interval
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._min:
                time.sleep(self._min - delta)
            self._last = time.monotonic()


_search_limiter = _RateLimiter(SEARCH_REQUEST_INTERVAL)
_default_limiter = _RateLimiter(DEFAULT_REQUEST_INTERVAL)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
_variants_lock = threading.Lock()


def _limiter_for(url: str) -> _RateLimiter:
    if "api.scryfall.com" in url and any(p in url for p in _SEARCH_PATHS):
        return _search_limiter
    return _default_limiter


def _retry_after_seconds(resp: requests.Response) -> float:
    """Seconds to wait after a 429: honour Retry-After, else the 30s cooldown."""
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(max(float(header), 1.0), 60.0)
        except ValueError:
            pass
    return RATE_LIMIT_COOLDOWN


def _http_get(url: str, params: dict | None = None) -> requests.Response:
    """Single rate-limited choke point for every Scryfall request.

    Routes to the correct per-endpoint limiter and backs off on HTTP 429 rather
    than hammering the API through its 30s penalty window.
    """
    limiter = _limiter_for(url)
    resp = None
    for attempt in range(MAX_RETRIES):
        limiter.wait()
        resp = _session.get(url, params=params, timeout=15)
        if resp.status_code == 429:
            wait = _retry_after_seconds(resp)
            _log.warning(
                "Scryfall 429 (attempt %d/%d); backing off %.0fs",
                attempt + 1, MAX_RETRIES, wait,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()  # exhausted retries while still 429
    return resp


# --- enumeration ------------------------------------------------------------

def _image_url(card: dict) -> str | None:
    uris = card.get("image_uris") or {}
    url = uris.get("png") or uris.get("large") or uris.get("normal")
    if url:
        return url
    # Double-faced cards carry image_uris per face; use the front face.
    faces = card.get("card_faces") or []
    if faces:
        face_uris = faces[0].get("image_uris") or {}
        return face_uris.get("png") or face_uris.get("large") or face_uris.get("normal")
    return None


def _query_scryfall_prints(expansion: str, name: str) -> list[ArtRef]:
    """Every printing/variation of ``name`` in ``expansion`` (paginated)."""
    front = name.split(" //", 1)[0]  # MDFCs: search the front face
    params: dict | None = {
        "q": f'set:{expansion.lower()} !"{front}"',
        "unique": "prints",
        "include_variations": "true",
    }
    url = f"{SCRYFALL_API}/cards/search"
    refs: list[ArtRef] = []
    while url:
        try:
            resp = _http_get(url, params=params)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []  # no cards match -> not in this set
            raise
        data = resp.json()
        for card in data.get("data", []):
            image_url = _image_url(card)
            if image_url:
                refs.append(ArtRef(card["id"], image_url, card.get("name", name)))
        url = data.get("next_page") if data.get("has_more") else None
        params = None  # next_page already carries the query
    return refs


def enumerate_set_cards(expansion: str) -> list[str]:
    """All distinct card names in ``expansion`` (one paginated Scryfall search).

    The live hot path warms artwork per-pack from the draft log; this exists for
    the manual "download a whole set" action, where there is no pack yet. Returns
    ``[]`` for an unknown set code.
    """
    params: dict | None = {"q": f"set:{expansion.lower()}", "unique": "cards"}
    url = f"{SCRYFALL_API}/cards/search"
    names: list[str] = []
    seen: set[str] = set()
    while url:
        try:
            resp = _http_get(url, params=params)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return []
            raise
        data = resp.json()
        for card in data.get("data", []):
            name = card.get("name")
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        url = data.get("next_page") if data.get("has_more") else None
        params = None  # next_page already carries the query
    return names


def _variants_path(expansion: str, cache_dir: Path) -> Path:
    return Path(cache_dir) / f"{expansion.upper()}_variants.json"


def _load_variants(expansion: str, cache_dir: Path) -> dict[str, list[dict]]:
    path = _variants_path(expansion, cache_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_variants(expansion: str, cache_dir: Path, data: dict) -> None:
    path = _variants_path(expansion, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def booster_artwork_ids(
    expansion: str, name: str, *, cache_dir: Path | None = None
) -> list[ArtRef]:
    """Every artwork that can appear in a booster of ``expansion`` for ``name``.

    Cache-first: the per-set enumeration is stored in ``<EXP>_variants.json`` so
    repeated calls (and the recognition hot path, once warmed) stay offline.
    """
    cache_dir = cache_dir or paths.scryfall_cache_dir()
    with _variants_lock:
        variants = _load_variants(expansion, cache_dir)
        cached = variants.get(name)
    if cached is not None:
        return [ArtRef(**ref) for ref in cached]

    refs = _query_scryfall_prints(expansion, name)
    with _variants_lock:
        variants = _load_variants(expansion, cache_dir)
        variants[name] = [asdict(ref) for ref in refs]
        _save_variants(expansion, cache_dir, variants)
    return refs


# --- image fetch ------------------------------------------------------------

def fetch_artwork(ref: ArtRef, cache_dir: Path) -> Path:
    """Cache-first download of ``ref``'s PNG; return the local path.

    Scryfall ids are globally unique, so images are cached flat as
    ``cache_dir/<id>.png``.
    """
    cache_dir = Path(cache_dir)
    out = cache_dir / f"{ref.scryfall_id}.png"
    if out.exists():
        return out

    resp = _http_get(ref.image_url)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".png.tmp")
    tmp.write_bytes(resp.content)
    os.replace(tmp, out)
    return out


def ensure_set_artwork(
    expansion: str, names: list[str], cache_dir: Path | None = None
) -> None:
    """Warm the cache for a whole draft (run in the background on draftStarted).

    Enumerates + downloads every artwork for each name, all rate-limited, so the
    recognition hot path is afterwards cache-only.
    """
    cache_dir = cache_dir or paths.scryfall_cache_dir()
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        try:
            refs = booster_artwork_ids(expansion, name, cache_dir=cache_dir)
            for ref in refs:
                fetch_artwork(ref, cache_dir)
        except requests.RequestException as exc:
            _log.warning("Artwork warm failed for %s: %s", name, exc)
    _log.info("Warmed artwork cache for %d names in %s", len(seen), expansion)
