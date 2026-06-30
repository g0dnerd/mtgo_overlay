"""17Lands' embargo on new-set curated data.

Their usage guidelines ask third-party tools not to surface a set's curated data
until the 12th day after its MTG Arena release, so traffic stays on their site
during peak engagement. We use the set's 17Lands data start date (from
``/data/filters``) as the release proxy and gate the live auto-fetch until the
window passes. The manual CSV path is the user's own download and is unaffected.

Always 12 days (the guidelines also allow 7 for short-term specialty sets, but
detecting those reliably isn't worth the risk of under-waiting). Fail-closed: a
start date we can't parse blocks the live fetch rather than guessing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

EMBARGO_DAYS = 12


def _parse(start_date: str | None) -> date | None:
    if not start_date:
        return None
    try:
        return datetime.strptime(start_date[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def lift_date(start_date: str | None) -> date | None:
    """The date the embargo lifts (start + :data:`EMBARGO_DAYS`), or ``None`` when
    the start date is missing/unparseable."""
    start = _parse(start_date)
    return start + timedelta(days=EMBARGO_DAYS) if start else None


def live_data_allowed(start_date: str | None, today: date) -> bool:
    """Whether the live 17Lands fetch may run for a set with this start date.

    ``True`` only once ``today`` reaches the lift date. Fail-closed: an unknown or
    unparseable start date returns ``False`` until the release date is known.
    """
    lift = lift_date(start_date)
    return lift is not None and today >= lift
