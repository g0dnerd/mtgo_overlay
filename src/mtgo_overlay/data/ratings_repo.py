"""Ratings repository: resolve GIH win rates for a pack with aggressive caching.

Source priority is configurable. With ``use_live=False`` (the sanctioned default)
the manual 17lands CSV is imported. With ``use_live=True`` the internal endpoint
is tried first, then CSV, then a stale cache, before giving up. Either way the
normalized result is cached as ``<EXP>_<FMT>.json`` carrying ``fetched_at`` so a
refresh happens at most once per set/format per 24h. Basic-land filtering lives
in :meth:`lookup`, keeping the overlay dumb.
"""

from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..system.logging_setup import get_logger
from .sets import is_basic_land
from .seventeenlands import SeventeenLandsClient, SeventeenLandsError

_log = get_logger("ratings")

TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class CardRating:
    name: str
    gih_wr: float | None  # percent, e.g. 75.7; None when unknown / low sample


class RatingsError(RuntimeError):
    pass


class RatingsRepository:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: SeventeenLandsClient | None = None,
        ttl_seconds: int = TTL_SECONDS,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.client = client
        self.ttl_seconds = ttl_seconds
        self._time = time_fn

    # --- cache plumbing ------------------------------------------------------

    def _cache_path(self, expansion: str, fmt: str) -> Path:
        return self.cache_dir / f"{expansion.upper()}_{fmt}.json"

    def _read_cache(self, expansion: str, fmt: str) -> dict | None:
        path = self._cache_path(expansion, fmt)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def is_fresh(self, expansion: str, fmt: str) -> bool:
        data = self._read_cache(expansion, fmt)
        if not data:
            return False
        fetched_at = data.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return False
        return (self._time() - fetched_at) < self.ttl_seconds

    def _csv_cache_current(self, expansion: str, fmt: str, csv_path: Path) -> bool:
        """True only if the cache was built from *this* CSV at/after its mtime.

        A changed ``csv_path`` or a touched file makes the cache stale regardless
        of TTL, so pointing at a new export takes effect on the next draft instead
        of being shadowed by a <24h-old cache.
        """
        data = self._read_cache(expansion, fmt)
        if not data or data.get("source") != "csv":
            return False
        if data.get("source_path") != str(csv_path):
            return False
        cached_mtime = data.get("source_mtime")
        if not isinstance(cached_mtime, (int, float)):
            return False
        try:
            return Path(csv_path).stat().st_mtime <= cached_mtime
        except OSError:
            return False

    def _write_cache(
        self,
        expansion: str,
        fmt: str,
        ratings: dict[str, float | None],
        source: str,
        *,
        source_path: str | None = None,
        source_mtime: float | None = None,
    ) -> Path:
        path = self._cache_path(expansion, fmt)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "expansion": expansion.upper(),
            "format": fmt,
            "source": source,
            "fetched_at": self._time(),
            "ratings": ratings,
        }
        if source_path is not None:
            payload["source_path"] = source_path
        if source_mtime is not None:
            payload["source_mtime"] = source_mtime
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        return path

    # --- acquisition ---------------------------------------------------------

    def ensure(
        self,
        expansion: str,
        fmt: str,
        *,
        use_live: bool,
        csv_path: Path | None = None,
    ) -> Path:
        """Make sure a <=24h-old ratings cache for ``expansion``/``fmt`` exists.

        Returns the cache path. Raises :class:`RatingsError` only when no source
        (live, CSV, or stale cache) can produce anything.
        """
        path = self._cache_path(expansion, fmt)
        # A configured CSV is the source of truth: rebuild whenever its path or
        # mtime differs from the cache, so the TTL never shadows a new export.
        prefer_csv = (
            not use_live and csv_path is not None and Path(csv_path).exists()
        )
        if prefer_csv:
            if self._csv_cache_current(expansion, fmt, csv_path):
                _log.info(
                    "Ratings cache current for %s/%s (CSV %s unchanged).",
                    expansion, fmt, csv_path,
                )
                return path
            if path.exists():
                _log.info(
                    "Ratings CSV for %s/%s changed (%s); re-importing.",
                    expansion, fmt, csv_path,
                )
        elif self.is_fresh(expansion, fmt):
            _log.info("Ratings cache fresh for %s/%s", expansion, fmt)
            return path

        if use_live and self.client is not None:
            try:
                data = self.client.fetch_ratings(expansion, fmt)
                ratings = SeventeenLandsClient.to_ratings_map(data)
                _log.info("Fetched %d ratings from 17lands for %s/%s",
                          len(ratings), expansion, fmt)
                return self._write_cache(expansion, fmt, ratings, source="17lands")
            except SeventeenLandsError as exc:
                _log.warning("17lands fetch failed (%s); falling back.", exc)

        if csv_path is not None and Path(csv_path).exists():
            p = Path(csv_path)
            ratings = parse_17lands_csv(p)
            try:
                mtime = p.stat().st_mtime
            except OSError:
                mtime = None
            _log.info("Imported %d ratings from CSV %s", len(ratings), csv_path)
            return self._write_cache(
                expansion, fmt, ratings, source="csv",
                source_path=str(p), source_mtime=mtime,
            )

        if path.exists():
            _log.warning("Using stale ratings cache for %s/%s", expansion, fmt)
            return path

        raise RatingsError(
            f"No ratings for {expansion}/{fmt}: no live data, no CSV, no cache."
        )

    # --- lookup --------------------------------------------------------------

    def lookup(self, expansion: str, fmt: str, names: list[str]) -> list[CardRating]:
        """Ratings for ``names``, basic lands dropped. Order follows ``names``."""
        data = self._read_cache(expansion, fmt) or {}
        ratings: dict[str, float | None] = data.get("ratings", {})
        out: list[CardRating] = []
        for name in names:
            if is_basic_land(name):
                continue
            out.append(CardRating(name=name, gih_wr=ratings.get(name)))
        return out

    def distribution(self, expansion: str, fmt: str) -> list[float]:
        """Sorted GIH WRs of every rated card in the set — the basis for coloring a
        pill by its *percentile within this set*, not by absolute thresholds."""
        data = self._read_cache(expansion, fmt) or {}
        ratings: dict[str, float | None] = data.get("ratings", {})
        return sorted(v for v in ratings.values() if isinstance(v, (int, float)))


def parse_17lands_csv(path: Path) -> dict[str, float | None]:
    """Parse a 17lands card-ratings CSV export into ``{name: GIH WR percent}``.

    Tolerates the UTF-8 BOM the site emits, empty GIH WR cells (low-sample cards
    -> ``None``), and stray whitespace.
    """
    out: dict[str, float | None] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            name = (row.get("Name") or "").strip()
            if not name:
                continue
            out[name] = _percent_to_float(row.get("GIH WR"))
    return out


def _percent_to_float(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = raw.strip().rstrip("%").strip()
    if not cleaned:
        return None
    try:
        return round(float(cleaned), 1)
    except ValueError:
        return None
