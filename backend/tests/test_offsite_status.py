from datetime import datetime, timedelta

from app.services.offsite_status import evaluate

NOW = datetime(2026, 10, 12, 12, 0, 0)


def test_off_without_waiver_is_a_problem():
    result = evaluate({"enabled": False}, NOW)
    assert result["state"] == "off" and result["problem"] is True


def test_waiver_is_visible_and_not_a_problem():
    result = evaluate({"enabled": False, "waiver": {"reason": "lab only", "by": "ops", "at": "2026-10-01T00:00:00"}}, NOW)
    assert result["state"] == "waived" and result["problem"] is False
    assert "lab only" in result["message"]


def test_enabled_but_never_run_is_a_problem():
    assert evaluate({"enabled": True}, NOW)["state"] == "never_run"


def test_last_run_failed_is_a_problem():
    result = evaluate({"enabled": True, "last_run": {"at": NOW.isoformat(), "ok": False, "summary": "host key"}}, NOW)
    assert result["state"] == "failed" and "host key" in result["message"]


def test_success_older_than_36_hours_is_stale():
    old = (NOW - timedelta(hours=40)).isoformat()
    assert evaluate({"enabled": True, "last_run": {"at": old, "ok": True}}, NOW)["state"] == "stale"


def test_recent_success_is_ok():
    recent = (NOW - timedelta(hours=5)).isoformat()
    result = evaluate({"enabled": True, "last_run": {"at": recent, "ok": True}}, NOW)
    assert result["state"] == "ok" and result["problem"] is False
