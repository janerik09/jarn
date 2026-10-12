from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services.maintenance import window_status


def _settings(start, end, zone="UTC"):
    return SimpleNamespace(maintenance_window_start=start, maintenance_window_end=end,
                           general_config={"timezone": zone})


def test_no_window_means_no_restriction():
    status = window_status(_settings(None, None), now=datetime(2026, 10, 11, 12, 0, tzinfo=ZoneInfo("UTC")))
    assert status["configured"] is False and status["inside"] is True


def test_daytime_window():
    s = _settings("02:00", "04:00")
    assert window_status(s, now=datetime(2026, 10, 11, 3, 0, tzinfo=ZoneInfo("UTC")))["inside"] is True
    assert window_status(s, now=datetime(2026, 10, 11, 5, 0, tzinfo=ZoneInfo("UTC")))["inside"] is False


def test_overnight_window_and_timezone():
    s = _settings("22:00", "02:00", zone="Europe/Oslo")
    # 23:30 Oslo time is inside an overnight window
    assert window_status(s, now=datetime(2026, 10, 11, 21, 30, tzinfo=ZoneInfo("UTC")))["inside"] is True
    assert window_status(s, now=datetime(2026, 10, 11, 12, 0, tzinfo=ZoneInfo("UTC")))["inside"] is False
