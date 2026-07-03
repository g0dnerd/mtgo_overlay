"""Prices repository: MTGO ticket prices for a set, cached with a 24h TTL.

Unlike ratings (keyed by card *name*), prices are keyed by Scryfall **printing
id** — the same card name has a different ``tix`` per printing/version, so the
overlay must show the price of the exact printing the recognizer matched. The
normalized ``{id: tix}`` map is cached as ``<EXP>_prices.json`` carrying
``fetched_at`` so a refresh happens at most once per set per 24h.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from ..recognition import scryfall_art
from ..system.logging_setup import get_logger

_log = get_logger("prices")

TTL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class CardPrice:
    printing_id: str
    tix: float | None  # MTGO tickets; None when the printing has no tix


class PricesRepository:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: Callable[[str], dict[str, float | None]] = scryfall_art.fetch_set_tix,
        ttl_seconds: int = TTL_SECONDS,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self._client = client
        self.ttl_seconds = ttl_seconds
        self._time = time_fn

    # --- cache plumbing ------------------------------------------------------

    def _cache_path(self, expansion: str) -> Path:
        return self.cache_dir / f"{expansion.upper()}_prices.json"

    def _read_cache(self, expansion: str) -> dict | None:
        path = self._cache_path(expansion)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def is_fresh(self, expansion: str) -> bool:
        data = self._read_cache(expansion)
        if not data:
            return False
        fetched_at = data.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return False
        return (self._time() - fetched_at) < self.ttl_seconds

    def _write_cache(self, expansion: str, prices: dict[str, float | None]) -> Path:
        path = self._cache_path(expansion)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "expansion": expansion.upper(),
            "fetched_at": self._time(),
            "prices": prices,
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        return path

    # --- acquisition ---------------------------------------------------------

    def ensure(self, expansion: str) -> Path:
        """Make sure a <=24h-old price cache for ``expansion`` exists.

        A fresh cache is kept; otherwise prices are refetched from Scryfall. On a
        fetch failure a stale cache is retained rather than discarded.
        """
        path = self._cache_path(expansion)
        if self.is_fresh(expansion):
            _log.info("Price cache fresh for %s", expansion)
            return path
        try:
            prices = self._client(expansion)
        except Exception as exc:  # noqa: BLE001 - network boundary
            if path.exists():
                _log.warning(
                    "Price fetch failed for %s (%s); keeping stale cache.",
                    expansion,
                    exc,
                )
                return path
            raise
        _log.info("Fetched %d printing price(s) for %s", len(prices), expansion)
        return self._write_cache(expansion, prices)

    # --- lookup --------------------------------------------------------------

    def lookup(self, expansion: str, printing_ids: Sequence[str]) -> list[CardPrice]:
        """Prices for ``printing_ids`` (order preserved). Unknown ids -> ``None``."""
        data = self._read_cache(expansion) or {}
        prices: dict[str, float | None] = data.get("prices", {})
        return [CardPrice(pid, prices.get(pid)) for pid in printing_ids]
