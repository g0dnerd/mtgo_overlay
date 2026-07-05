"""Goatbots MTGO price feed.

Goatbots publishes its daily average sell (retail) prices once a day as a
single zipped JSON keyed by Magic Online catalog id — the price MTGO players
actually reference on goatbots.com, unlike Scryfall's Cardhoarder-sourced
``tix`` (which runs high and stale for freshly released draft sets). The feed
covers *every* MTGO object in one file, so callers cache it once and look prices
up by the ``mtgo_id`` Scryfall exposes per printing.

Goatbots asks only that reusers link back to their homepage; the overlay does so
in the tray menu and the price-pill caption.
"""

from __future__ import annotations

import io
import json
import zipfile

import requests

from ..system.logging_setup import get_logger

_log = get_logger("goatbots")

PRICES_URL = "https://www.goatbots.com/download/prices/price-history.zip"
HOMEPAGE = "https://www.goatbots.com/"
USER_AGENT = (
    "MtgoOverlay/0.3 (+https://github.com/g0dnerd/mtgo_overlay; "
    "MTGO draft overlay; personal use)"
)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


class GoatbotsError(RuntimeError):
    pass


def fetch_prices(session: requests.Session | None = None) -> dict[str, float]:
    """Download + parse the latest Goatbots feed into ``{mtgo_id: tix}``.

    Keys are the MTGO catalog id as a **string** (matching the feed and JSON
    cache round-trips). Non-numeric prices are dropped. Raises
    :class:`GoatbotsError` on a malformed payload.
    """
    sess = session or _session
    resp = sess.get(PRICES_URL, timeout=30)
    resp.raise_for_status()
    try:
        archive = zipfile.ZipFile(io.BytesIO(resp.content))
        name = archive.namelist()[0]
        # The feed ships with a UTF-8 BOM.
        data = json.loads(archive.read(name).decode("utf-8-sig"))
    except (zipfile.BadZipFile, IndexError, ValueError) as exc:
        raise GoatbotsError(f"malformed Goatbots price payload: {exc}") from exc
    prices = {
        str(mid): float(price)
        for mid, price in data.items()
        if isinstance(price, (int, float))
    }
    _log.info("Fetched %d Goatbots prices.", len(prices))
    return prices
