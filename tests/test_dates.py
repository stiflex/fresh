from datetime import datetime, timezone

import pytest
from freezegun import freeze_time

from dates import current_week, week_bounds, week_from_date


def test_week_bounds_mid_year():
    start, end = week_bounds("2026-W34")
    assert start == datetime(2026, 8, 17, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 24, tzinfo=timezone.utc)


def test_week_bounds_year_boundary():
    start, end = week_bounds("2026-W01")
    assert start == datetime(2025, 12, 29, tzinfo=timezone.utc)
    assert end == datetime(2026, 1, 5, tzinfo=timezone.utc)


def test_week_bounds_rejects_bad_format():
    with pytest.raises(ValueError):
        week_bounds("2026-34")


@freeze_time("2026-08-24 10:00:00")  # a Monday
def test_current_week_on_monday_returns_prior_week():
    assert current_week() == "2026-W34"


@freeze_time("2026-08-20 10:00:00")  # a Thursday, mid-week
def test_current_week_midweek_returns_prior_complete_week():
    assert current_week() == "2026-W33"


def test_week_from_date_mid_week():
    assert week_from_date("2026-08-24") == "2026-W35"


def test_week_from_date_monday_itself():
    assert week_from_date("2026-08-17") == "2026-W34"


def test_week_from_date_year_boundary():
    assert week_from_date("2025-12-29") == "2026-W01"


def test_week_from_date_rejects_bad_format():
    with pytest.raises(ValueError):
        week_from_date("24-08-2026")
