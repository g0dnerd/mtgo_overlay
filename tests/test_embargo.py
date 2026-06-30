from datetime import date

from mtgo_overlay.data import embargo


def test_lift_date_adds_twelve_days():
    assert embargo.lift_date("2026-06-01") == date(2026, 6, 13)


def test_lift_date_ignores_time_suffix():
    assert embargo.lift_date("2026-06-01T00:00:00Z") == date(2026, 6, 13)


def test_lift_date_none_for_missing_or_garbage():
    assert embargo.lift_date(None) is None
    assert embargo.lift_date("") is None
    assert embargo.lift_date("not-a-date") is None


def test_live_blocked_during_window():
    start = "2026-06-01"
    assert not embargo.live_data_allowed(start, date(2026, 6, 1))  # release day
    assert not embargo.live_data_allowed(start, date(2026, 6, 12))  # day before lift


def test_live_allowed_on_and_after_lift_day():
    start = "2026-06-01"
    assert embargo.live_data_allowed(start, date(2026, 6, 13))  # exactly the lift
    assert embargo.live_data_allowed(start, date(2026, 7, 1))


def test_unknown_start_date_fails_closed():
    assert not embargo.live_data_allowed(None, date(2026, 6, 30))
    assert not embargo.live_data_allowed("garbage", date(2026, 6, 30))
