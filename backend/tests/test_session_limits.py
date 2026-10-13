from datetime import datetime, timedelta

from app.services.session_limits import ABSOLUTE_REASON, IDLE_REASON, session_timeout

NOW = datetime(2026, 10, 13, 8, 0, 0)


def test_active_session_is_allowed():
    assert session_timeout(NOW - timedelta(hours=2), NOW - timedelta(minutes=5), NOW, 60, 12) is None


def test_session_idle_past_the_limit_ends():
    reason = session_timeout(NOW - timedelta(hours=10), NOW - timedelta(minutes=61), NOW, 60, 12)
    assert reason == IDLE_REASON


def test_idle_exactly_at_the_limit_is_still_allowed():
    assert session_timeout(NOW - timedelta(hours=1), NOW - timedelta(minutes=60), NOW, 60, 12) is None


def test_session_older_than_the_cap_ends_even_when_in_use():
    reason = session_timeout(NOW - timedelta(hours=12, minutes=1), NOW, NOW, 60, 12)
    assert reason == ABSOLUTE_REASON


def test_cap_is_reported_before_idle_when_both_apply():
    reason = session_timeout(NOW - timedelta(hours=13), NOW - timedelta(hours=5), NOW, 60, 12)
    assert reason == ABSOLUTE_REASON
