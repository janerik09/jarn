from datetime import datetime

import pytest

from app.services import cron


def test_weekly_sunday_0200_matches_only_that_time():
    assert cron.matches("0 2 * * 0", datetime(2026, 10, 11, 2, 0))      # a Sunday
    assert not cron.matches("0 2 * * 0", datetime(2026, 10, 12, 2, 0))  # a Monday
    assert not cron.matches("0 2 * * 0", datetime(2026, 10, 11, 2, 1))


def test_sunday_is_0_or_7():
    assert cron.matches("0 2 * * 7", datetime(2026, 10, 11, 2, 0))


def test_steps_and_ranges():
    assert cron.matches("*/15 * * * *", datetime(2026, 10, 11, 3, 45))
    assert not cron.matches("*/15 * * * *", datetime(2026, 10, 11, 3, 46))
    assert cron.matches("0 9-17 * * 1-5", datetime(2026, 10, 12, 10, 0))   # Monday 10:00
    assert not cron.matches("0 9-17 * * 1-5", datetime(2026, 10, 11, 10, 0))  # Sunday


def test_day_of_month_and_weekday_combine_as_or_when_both_restricted():
    # "1st of the month or any Friday"
    assert cron.matches("0 0 1 * 5", datetime(2026, 10, 1, 0, 0))   # the 1st, a Thursday
    assert cron.matches("0 0 1 * 5", datetime(2026, 10, 9, 0, 0))   # a Friday, not the 1st
    assert not cron.matches("0 0 1 * 5", datetime(2026, 10, 8, 0, 0))


@pytest.mark.parametrize("bad", ["0 2 * *", "60 2 * * *", "0 2 * * 8", "*/0 * * * *", "a b c d e"])
def test_invalid_expressions_are_rejected(bad):
    with pytest.raises(ValueError):
        cron.parse(bad)
