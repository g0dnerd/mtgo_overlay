"""The set of expansions 17Lands supports, cached to disk for the tray picker.

17Lands' ``/data/filters`` endpoint lists every expansion it has data for, the
formats per set, and each set's start date. We cache the parsed payload (TTL ~7
days; the list changes only on release) so opening the "Download set…" menu never
touches the network, and expose pure helpers to order the dropdown (newest first)
and pick a sane ratings format for a prefetch. Mirrors
:class:`RatingsRepository`'s ``client`` / ``cache_dir`` / ``time_fn`` convention.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from ..system.logging_setup import get_logger
from .seventeenlands import SeventeenLandsClient, SeventeenLandsError

_log = get_logger("expansions")

TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_FORMAT = "PremierDraft"
CACHE_FILENAME = "supported_sets.json"


class SupportedSets:
    def __init__(
        self,
        client: SeventeenLandsClient | None,
        cache_dir: Path,
        *,
        ttl_seconds: int = TTL_SECONDS,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self._time = time_fn

    def _cache_path(self) -> Path:
        return self.cache_dir / CACHE_FILENAME

    def _read_cache(self) -> dict | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _is_fresh(self, cached: dict | None) -> bool:
        if not cached:
            return False
        fetched_at = cached.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return False
        return (self._time() - fetched_at) < self.ttl_seconds

    def _write_cache(self, filters: dict) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": self._time(), "filters": filters}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)

    def ensure(self) -> dict:
        """The 17Lands filters dict, cache-first and TTL'd.

        Never raises: on a network/parse failure it serves a stale cache if one
        exists, else returns ``{}`` so the picker degrades to free-text entry
        instead of blocking the user.
        """
        cached = self._read_cache()
        if self._is_fresh(cached):
            return cached.get("filters", {})
        if self.client is not None:
            try:
                filters = self.client.fetch_filters()
                self._write_cache(filters)
                _log.info(
                    "Fetched 17Lands supported-set list (%d expansions).",
                    len(filters.get("expansions", [])),
                )
                return filters
            except SeventeenLandsError as exc:
                _log.warning("17Lands filters fetch failed (%s); using cache if any.", exc)
        if cached:
            _log.info("Using cached 17Lands supported-set list.")
            return cached.get("filters", {})
        return {}


# Arena-only sets whose 17Lands format coverage otherwise mimics a real MTGO set.
_ARENA_ONLY_CODES = frozenset({"HBG", "KLR", "SIR"})

# A set MTGO actually runs offers both of these on 17Lands; Alchemy rebalances and
# the cube/chaos event pseudo-sets never list both.
_MTGO_REQUIRED_FORMATS = frozenset({"PremierDraft", "TradDraft"})


def is_mtgo_draftable(code: str, formats: list[str]) -> bool:
    """Whether a 17Lands expansion is also a real MTGO-draftable set.

    17Lands lists Arena's whole set universe; this keeps only what MTGO gets.
    Dropped: Alchemy rebalances (``Y\\d\\d`` codes, no TradDraft), the Arena
    cube/chaos/remix event pseudo-sets (mixed-case or spaced names), and the
    handful of Arena-only remasters that otherwise look like real set codes.
    """
    if not (code.isalnum() and code.isupper()):
        return False
    if code in _ARENA_ONLY_CODES:
        return False
    return _MTGO_REQUIRED_FORMATS <= set(formats)


def codes_newest_first(filters: dict, *, mtgo_only: bool = False) -> list[str]:
    """Expansion codes ordered newest-first by 17Lands start date.

    Codes with no start date sort last; the blank pseudo-expansion is dropped.
    With ``mtgo_only`` the list is narrowed to MTGO-draftable sets
    (see :func:`is_mtgo_draftable`).
    """
    expansions = [c for c in filters.get("expansions", []) if c]
    start_dates = filters.get("start_dates", {})
    if mtgo_only:
        fbe = filters.get("formats_by_expansion", {})
        expansions = [c for c in expansions if is_mtgo_draftable(c, fbe.get(c, []))]
    return sorted(expansions, key=lambda c: start_dates.get(c, ""), reverse=True)


def format_for(expansion: str, preferred_fmt: str, filters: dict) -> str:
    """Pick a ratings format for ``expansion``: the preferred one when the set
    supports it, else the set's first listed format, else ``PremierDraft``."""
    formats = filters.get("formats_by_expansion", {}).get(expansion.upper(), [])
    if preferred_fmt in formats:
        return preferred_fmt
    if formats:
        return formats[0]
    return DEFAULT_FORMAT
