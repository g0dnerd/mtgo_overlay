"""Thin HTTP client for 17lands' internal card-ratings endpoint.

``GET https://www.17lands.com/card_ratings/data?expansion=<EXP>&format=<FMT>``
returns a JSON array; each element has a ``name`` and an ``ever_drawn_win_rate``
(0..1), which is the Game-in-Hand Win Rate shown by the site. This endpoint is
undocumented/internal (the same data the site's "download to CSV" exposes), not
an official API — callers pass a polite identifying ``User-Agent`` and the
repository above caps usage at one request per set/format per 24h.
"""

from __future__ import annotations

import requests

BASE_URL = "https://www.17lands.com/card_ratings/data"


class SeventeenLandsError(RuntimeError):
    """Any failure talking to or parsing a response from the endpoint."""


class SeventeenLandsClient:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._session = session or requests.Session()

    def fetch_ratings(
        self,
        expansion: str,
        fmt: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        params = {"expansion": expansion.upper(), "format": fmt}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        try:
            resp = self._session.get(
                BASE_URL, params=params, headers=headers, timeout=self.timeout
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise SeventeenLandsError(f"17lands request failed: {exc}") from exc
        except ValueError as exc:  # json decode
            raise SeventeenLandsError(f"17lands returned non-JSON: {exc}") from exc
        if not isinstance(data, list):
            raise SeventeenLandsError("17lands response was not a JSON array")
        return data

    @staticmethod
    def gih_win_rate(item: dict) -> float | None:
        """GIH WR as a percentage rounded to 0.1, or ``None`` for low-sample cards."""
        raw = item.get("ever_drawn_win_rate")
        if raw is None:
            return None
        try:
            return round(float(raw) * 100, 1)
        except (TypeError, ValueError):
            return None

    @classmethod
    def to_ratings_map(cls, data: list[dict]) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for item in data:
            name = (item.get("name") or "").strip()
            if name:
                out[name] = cls.gih_win_rate(item)
        return out
