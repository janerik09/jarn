import asyncio

import pytest

from app.connectors.base import CommandResult
from app.services.job_rollback import plan_job_rollback
from app.services.runner_limits import CONNECT_ATTEMPTS, concurrency_limit, connect_with_retry, run_limited


def _exec(status="success", metadata=None, dry_run=False, eid="e1"):
    return {"execution_id": eid, "server_id": "s1", "hostname": "web1", "status": status,
            "metadata": metadata or {}, "dry_run": dry_run}


GOOD_RECORD = {
    "packages": ["openssl", "linux-image-6.1.0-1", "curl"],
    "previous_versions": {"openssl": "3.0.1", "curl": "8.1", "linux-image-6.1.0-1": "6.1.0"},
    "rollback_possible": True,
}


def test_eligible_server_gets_non_kernel_packages_and_kernel_is_reported():
    plan = plan_job_rollback([_exec(metadata={"rollback": GOOD_RECORD})])
    item = plan["eligible"][0]
    assert item["packages"] == ["openssl", "curl"]
    assert item["previous_versions"] == {"openssl": "3.0.1", "curl": "8.1"}
    assert item["kernel_left_installed"] == ["linux-image-6.1.0-1"]


def test_runs_without_recorded_versions_are_skipped_with_reason():
    plan = plan_job_rollback([_exec(metadata={})])
    assert not plan["eligible"]
    assert "did not record" in plan["skipped"][0]["reason"]


def test_failed_or_rolled_back_or_dry_run_servers_are_not_rolled_back():
    assert "nothing to roll back" in plan_job_rollback([_exec("failed", {"rollback": GOOD_RECORD})])["skipped"][0]["reason"]
    assert "already" in plan_job_rollback([_exec("rolled_back", {"rollback": GOOD_RECORD})])["skipped"][0]["reason"]
    assert "dry run" in plan_job_rollback([_exec(metadata={"rollback": GOOD_RECORD}, dry_run=True)])["skipped"][0]["reason"]


def test_second_rollback_of_same_server_is_refused():
    done = {"rollback": GOOD_RECORD, "manual_rollback": {"ok": True}}
    assert "already rolled back" in plan_job_rollback([_exec(metadata=done)])["skipped"][0]["reason"]


def test_versions_no_longer_offered_block_rollback():
    record = {**GOOD_RECORD, "rollback_possible": False}
    assert "no longer offered" in plan_job_rollback([_exec(metadata={"rollback": record})])["skipped"][0]["reason"]


def test_only_kernel_changes_are_skipped():
    record = {"packages": ["linux-image-6.1.0-1"], "previous_versions": {"linux-image-6.1.0-1": "6.1.0"},
              "rollback_possible": True}
    assert "only kernel" in plan_job_rollback([_exec(metadata={"rollback": record})])["skipped"][0]["reason"]


def test_concurrency_limit_is_never_exceeded():
    running = {"now": 0, "peak": 0}

    async def worker(_):
        running["now"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1

    asyncio.run(run_limited(list(range(12)), 3, worker))
    assert running["peak"] == 3


def test_concurrency_setting_defaults_and_floors():
    assert concurrency_limit(None) == 5
    assert concurrency_limit("bad") == 5
    assert concurrency_limit(0) == 1
    assert concurrency_limit("4") == 4


def test_connect_retries_then_succeeds_and_waits_between_attempts():
    outcomes = iter([False, False, True])
    waits = []

    async def connect():
        return next(outcomes)

    async def sleep(seconds):
        waits.append(seconds)

    assert asyncio.run(connect_with_retry(connect, sleep)) is True
    assert waits == [10, 30]


def test_connect_gives_up_after_all_attempts():
    calls = []

    async def connect():
        calls.append(1)
        return False

    async def sleep(_):
        pass

    assert asyncio.run(connect_with_retry(connect, sleep)) is False
    assert len(calls) == CONNECT_ATTEMPTS


def test_preflight_blockers_include_early_connectivity_failures():
    from app.services.preflight_service import PreflightChecker
    results = {"s1": {"ready": False, "checks": {"connectivity": "✗ failed: connection refused"}}}
    blockers = PreflightChecker._extract_blockers(results)
    assert blockers == [{"server_id": "s1", "issues": ["✗ failed: connection refused"]}]


def _apt_bundles(listing_versions):
    from app.connectors.ssh import SSHConnector
    conn = SSHConnector(hostname="test-host", username="u")
    lines = "\n".join(f"{name}/stable {version} amd64 [upgradable from: 1.0]" for name, version in listing_versions.items())
    calls = []

    async def fake_execute(command, timeout=30):
        calls.append(command)
        if "--simulate" in command and "grep '^Inst'" in command:
            return CommandResult(0, "\n".join(listing_versions), "", 0)
        return CommandResult(0, "", "", 0)

    conn.execute_command = fake_execute
    return asyncio.run(conn._create_apt_patch_bundles(lines))


def test_bundle_name_identifies_content_not_scan_position():
    first = _apt_bundles({"jarn-canary-test": "1.0"})
    again = _apt_bundles({"jarn-canary-test": "1.0"})
    newer = _apt_bundles({"jarn-canary-test": "2.0"})
    assert first[0].name == again[0].name
    assert first[0].name != newer[0].name
    import re
    assert re.fullmatch(r"Bundle \S+ [0-9a-f]{8}", first[0].name)
