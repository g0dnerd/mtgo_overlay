"""Prices repository: Goatbots MTGO ticket prices, cached with a 6h TTL.

Goatbots publishes one daily JSON of ``{mtgo_id: tix}`` for *all* MTGO cards
(not per set), so this caches a single global map as ``goatbots_prices.json``
and resolves a printing's price by the Magic Online catalog id Scryfall exposes
as ``mtgo_id`` (see :func:`recognition.scryfall_art.set_mtgo_ids`). Prices are
retail (what you pay to buy from Goatbots), the number goatbots.com shows.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..system.logging_setup import get_logger
from . import goatbots

_log = get_logger("prices")

TTL_SECONDS = 6 * 60 * 60


@dataclass(frozen=True)
class CardPrice:
    printing_id: str  # Scryfall printing id, so lookups join back to CardLocation
    tix: float | None  # MTGO tickets; None when the printing has no Goatbots price


class PricesRepository:
    def __init__(
        self,
        cache_dir: Path,
        *,
        client: Callable[[], dict[str, float]] = goatbots.fetch_prices,
        ttl_seconds: int = TTL_SECONDS,
        time_fn: Callable[[], float] = time.time,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self._client = client
        self.ttl_seconds = ttl_seconds
        self._time = time_fn
        self._prices_cache: dict[str, float] | None = None

    # --- cache plumbing ------------------------------------------------------

    def _cache_path(self) -> Path:
        return self.cache_dir / "goatbots_prices.json"

    def _read_cache(self) -> dict | None:
        path = self._cache_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def is_fresh(self) -> bool:
        data = self._read_cache()
        if not data:
            return False
        fetched_at = data.get("fetched_at")
        if not isinstance(fetched_at, (int, float)):
            return False
        return (self._time() - fetched_at) < self.ttl_seconds

    def _write_cache(self, prices: dict[str, float]) -> Path:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"fetched_at": self._time(), "prices": prices}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
        return path

    # --- acquisition ---------------------------------------------------------

    def ensure(self) -> Path:
        """Make sure a <=6h-old Goatbots price cache exists.

        A fresh cache is kept; otherwise the feed is refetched. On a fetch failure
        a stale cache is retained rather than discarded.
        """
        path = self._cache_path()
        if self.is_fresh():
            _log.info("Goatbots price cache fresh.")
            return path
        try:
            prices = self._client()
        except Exception as exc:  # noqa: BLE001 - network boundary
            if path.exists():
                _log.warning(
                    "Goatbots price fetch failed (%s); keeping stale cache.", exc
                )
                return path
            raise
        self._prices_cache = None  # force a reload from the freshly written file
        _log.info("Cached %d Goatbots prices.", len(prices))
        return self._write_cache(prices)

    # --- lookup --------------------------------------------------------------

    def _prices(self) -> dict[str, float]:
        if self._prices_cache is None:
            self._prices_cache = (self._read_cache() or {}).get("prices", {})
        return self._prices_cache

    def price_for(self, mtgo_id: int | str | None) -> float | None:
        """The Goatbots tix for an MTGO catalog id, or ``None`` if unknown."""
        if mtgo_id is None:
            return None
        return self._prices().get(str(mtgo_id))

    def lookup(
        self, printings: Iterable[tuple[str, int | None]]
    ) -> list[CardPrice]:
        """Prices for ``(scryfall_id, mtgo_id)`` pairs, keyed back by Scryfall id."""
        return [CardPrice(sid, self.price_for(mid)) for sid, mid in printings]
