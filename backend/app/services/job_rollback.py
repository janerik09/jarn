"""
Plans a manual rollback of a finished patch job.

A server can be rolled back only when the run recorded the package versions it had before patching, the versions
are still offered by a repository, and the run was not a dry run. Kernel packages (linux-*) are never downgraded by
package: a kernel change takes effect on reboot and is left as installed. Nothing in this module touches a server.
"""

from typing import Any, Dict, List

KERNEL_PREFIX = "linux-"


def plan_job_rollback(executions: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """executions: dicts with execution_id, server_id, hostname, status, metadata and dry_run.

    Returns {"eligible": [...], "skipped": [...]}. Each eligible item lists the packages to downgrade, their target
    versions, and any kernel packages that are left as installed.
    """
    eligible: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for item in executions:
        base = {"execution_id": item["execution_id"], "server_id": item["server_id"], "hostname": item["hostname"]}
        status = item.get("status")
        metadata = item.get("metadata") or {}
        if status == "rolled_back" or (metadata.get("manual_rollback") or {}).get("ok"):
            skipped.append({**base, "reason": "already rolled back"})
            continue
        if status != "success":
            skipped.append({**base, "reason": f"server is {status}, nothing to roll back"})
            continue
        record = metadata.get("rollback")
        if not record:
            skipped.append({**base, "reason": "the run did not record the previous package versions (it ran before rollback was recorded)"})
            continue
        if item.get("dry_run"):
            skipped.append({**base, "reason": "dry run: nothing was installed"})
            continue
        if not record.get("rollback_possible"):
            skipped.append({**base, "reason": "a previous version is no longer offered by any repository"})
            continue
        previous = record.get("previous_versions") or {}
        packages = [p for p in record.get("packages", []) if p in previous and not p.startswith(KERNEL_PREFIX)]
        kernel = [p for p in record.get("packages", []) if p.startswith(KERNEL_PREFIX)]
        if not packages:
            skipped.append({**base, "reason": "only kernel packages changed; kernels are not downgraded by package"})
            continue
        eligible.append({**base, "packages": packages,
                         "previous_versions": {p: previous[p] for p in packages},
                         "kernel_left_installed": kernel})
    return {"eligible": eligible, "skipped": skipped}
