"""Ratings repository: resolve GIH win rates for a pack with aggressive caching.

Source priority is configurable. With ``use_live=False`` (the sanctioned default)
the manual 17lands CSV is imported. With ``use_live=True`` the internal endpoint
is tried first, then CSV, then a stale cache, before giving up. Either way the
normalized result is cached as ``<EXP>_<FMT>_<group>.json`` carrying ``fetched_at``
so a refresh happens at most once per set/format/group per 24h. The ``group`` axis
("all" vs "top") keys the two 17lands player cohorts into separate caches so the
overlay's toggle flips between them without a network round-trip. Basic-land
filtering lives in :meth:`lookup`, keeping the overlay dumb.
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

# Stable cache labels for the two 17lands player cohorts. "all" omits the
# endpoint's user_group param (its aggregate); "top" selects top players.
GROUP_ALL = "all"
GROUP_TOP = "top"


def _user_group_param(group: str) -> str | None:
    """The endpoint's ``user_group`` value for a cache group label.

    "all" maps to ``None`` (omit the param — sending ``user_group=all`` returns
    null win rates); any other label is sent verbatim.
    """
    return None if group == GROUP_ALL else group


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

    def _cache_path(self, expansion: str, fmt: str, group: str = GROUP_ALL) -> Path:
        return self.cache_dir / f"{expansion.upper()}_{fmt}_{group}.json"

    def _read_cache(
        self, expansion: str, fmt: str, group: str = GROUP_ALL
    ) -> dict | None:
        path = self._cache_path(expansion, fmt, group)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def is_fresh(self, expansion: str, fmt: str, group: str = GROUP_ALL) -> bool:
        data = self._read_cache(expansion, fmt, group)
        if not data:
            return False
        fetched_at = data.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return False
        return (self._time() - fetched_at) < self.ttl_seconds

    def _csv_cache_current(
        self, expansion: str, fmt: str, csv_path: Path, group: str = GROUP_ALL
    ) -> bool:
        """True only if the cache was built from *this* CSV at/after its mtime.

        A changed ``csv_path`` or a touched file makes the cache stale regardless
        of TTL, so pointing at a new export takes effect on the next draft instead
        of being shadowed by a <24h-old cache.
        """
        data = self._read_cache(expansion, fmt, group)
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
        group: str = GROUP_ALL,
        source_path: str | None = None,
        source_mtime: float | None = None,
    ) -> Path:
        path = self._cache_path(expansion, fmt, group)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "expansion": expansion.upper(),
            "format": fmt,
            "group": group,
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

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(self._time()))

    def ensure(
        self,
        expansion: str,
        fmt: str,
        *,
        use_live: bool,
        group: str = GROUP_ALL,
        csv_path: Path | None = None,
        start_date: str | None = None,
    ) -> Path:
        """Make sure a <=24h-old ratings cache for ``expansion``/``fmt``/``group``
        exists.

        Returns the cache path. Raises :class:`RatingsError` only when no source
        (live, CSV, or stale cache) can produce anything. ``group`` is the player
        cohort ("all" or "top"); it keys a separate cache and selects the live
        ``user_group`` filter. ``start_date`` (``YYYY-MM-DD``, the set's 17lands
        start) makes the live fetch span the set's whole lifetime instead of
        17lands' rolling default window, which is empty for sets no longer in
        rotation. A CSV has no cohort dimension, so it seeds whichever ``group``
        asked for it.
        """
        path = self._cache_path(expansion, fmt, group)
        # A configured CSV is the source of truth: rebuild whenever its path or
        # mtime differs from the cache, so the TTL never shadows a new export.
        prefer_csv = (
            not use_live and csv_path is not None and Path(csv_path).exists()
        )
        if prefer_csv:
            if self._csv_cache_current(expansion, fmt, csv_path, group):
                _log.info(
                    "Ratings cache current for %s/%s/%s (CSV %s unchanged).",
                    expansion, fmt, group, csv_path,
                )
                return path
            if path.exists():
                _log.info(
                    "Ratings CSV for %s/%s/%s changed (%s); re-importing.",
                    expansion, fmt, group, csv_path,
                )
        elif self.is_fresh(expansion, fmt, group):
            _log.info("Ratings cache fresh for %s/%s/%s", expansion, fmt, group)
            return path

        if use_live and self.client is not None:
            try:
                end_date = self._today() if start_date else None
                data = self.client.fetch_ratings(
                    expansion, fmt, start_date=start_date, end_date=end_date,
                    user_group=_user_group_param(group),
                )
                ratings = SeventeenLandsClient.to_ratings_map(data)
                if not any(v is not None for v in ratings.values()):
                    # A 200 with every WR null (e.g. an empty date window for a
                    # rotated-out set) is a miss, not data — fall back rather than
                    # caching all-N/A and shadowing the CSV for 24h.
                    raise SeventeenLandsError(
                        f"no rated cards for {expansion}/{fmt}/{group} "
                        f"(window {start_date or 'default'}..{end_date or 'now'})"
                    )
                _log.info("Fetched %d ratings from 17lands for %s/%s/%s",
                          len(ratings), expansion, fmt, group)
                return self._write_cache(
                    expansion, fmt, ratings, source="17lands", group=group
                )
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
                expansion, fmt, ratings, source="csv", group=group,
                source_path=str(p), source_mtime=mtime,
            )

        if path.exists():
            _log.warning("Using stale ratings cache for %s/%s/%s", expansion, fmt, group)
            return path

        raise RatingsError(
            f"No ratings for {expansion}/{fmt}/{group}: no live data, no CSV, no cache."
        )

    # --- lookup --------------------------------------------------------------

    def lookup(
        self, expansion: str, fmt: str, names: list[str], group: str = GROUP_ALL
    ) -> list[CardRating]:
        """Ratings for ``names``, basic lands dropped. Order follows ``names``."""
        data = self._read_cache(expansion, fmt, group) or {}
        ratings: dict[str, float | None] = data.get("ratings", {})
        out: list[CardRating] = []
        for name in names:
            if is_basic_land(name):
                continue
            out.append(CardRating(name=name, gih_wr=ratings.get(name)))
        return out

    def distribution(
        self, expansion: str, fmt: str, group: str = GROUP_ALL
    ) -> list[float]:
        """Sorted GIH WRs of every rated card in the set — the basis for coloring a
        pill by its *percentile within this set*, not by absolute thresholds."""
        data = self._read_cache(expansion, fmt, group) or {}
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
