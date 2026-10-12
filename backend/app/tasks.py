import asyncio
import math
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.celery_app import app
from app.database import AsyncSessionLocal
from app.models import PatchJob, PatchJobExecution, Server, ExecutionStatus, JobStatus
from app.connectors import SSHConnector
from app.connectors.ssh import pinned_host_key
from app.logging_config import get_logger
from app.services.scan_snapshot import build_snapshot
from app.services.backup_providers import BackupError, provider_from_config, mapping_from_server
from app.services.runner_limits import concurrency_limit, connect_with_retry, run_limited
from app.services.job_rollback import plan_job_rollback

# Patch selection for scheduled runs: every package the server reports as upgradable
ALL_UPGRADABLE = "__all_upgradable__"

# Allow nested event loops for Celery+async compatibility (with uvloop support)
try:
    import nest_asyncio
    nest_asyncio.apply()
except (ImportError, ValueError):
    # nest_asyncio not available or can't patch (e.g., with uvloop)
    # Fallback: will handle event loop conflicts in execute_patch_job
    pass

logger = get_logger(__name__)


@app.task(bind=True, max_retries=0)
def execute_patch_job(self, job_id: str, resume: bool = False):
    """Execute patch job for all servers. resume=True continues a rollout paused after the canary."""
    try:
        # Try to run directly first
        try:
            asyncio.run(async_execute_job(job_id, resume))
        except RuntimeError as e:
            # If event loop conflict, run in thread pool
            if "attached to a different loop" in str(e):
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(asyncio.run, async_execute_job(job_id, resume)).result()
            else:
                raise
    except Exception as exc:
        logger.error("task_execution_error", job_id=job_id, error=str(exc), exc_info=True)
        raise


async def async_execute_job(job_id: str, resume: bool = False):
    """Async job execution logic"""
    from app.services import audit
    audit.correlation_var.set(job_id)
    await audit.record("job.resume" if resume else "job.start", category="job", resource_type="job", resource_id=job_id,
                       details={"resumed": resume})
    async with AsyncSessionLocal() as db:
        # Get job
        result = await db.execute(
            select(PatchJob).where(PatchJob.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            logger.error("job_not_found", job_id=job_id)
            return

        if not resume:
            # Update job status to running
            job.status = JobStatus.running
            job.start_time = datetime.utcnow()
            await db.commit()

            logger.info("job_execution_started", job_id=job_id)

            # Create execution records for each server
            import uuid
            for server_id in job.server_ids:
                if isinstance(server_id, str):
                    sid = uuid.UUID(server_id)
                else:
                    sid = server_id

                execution = PatchJobExecution(
                    job_id=job.id,
                    server_id=sid,
                    status=ExecutionStatus.pending
                )
                db.add(execution)

            await db.commit()
        else:
            logger.info("job_execution_resumed", job_id=job_id)

        # Every execution of this job. Waves are laid out from all of them, so a resumed run keeps the same
        # split, and only the executions still pending are run.
        result = await db.execute(select(PatchJobExecution).where(PatchJobExecution.job_id == job_id))
        every_execution = result.scalars().all()
        pending_ids = {e.id for e in every_execution if e.status == ExecutionStatus.pending}

        k8s_hosts = set((job.metadata_ or {}).get("kubernetes_order", []))
        hostnames = {}
        for execution in every_execution:
            srv = (await db.execute(select(Server).where(Server.id == execution.server_id))).scalar_one_or_none()
            hostnames[execution.id] = srv.hostname if srv else ""
        k8s_all = sorted((e for e in every_execution if hostnames[e.id] in k8s_hosts), key=lambda e: hostnames[e.id])
        k8s_executions = [e for e in k8s_all if e.id in pending_ids]
        other_executions = sorted((e for e in every_execution if hostnames[e.id] not in k8s_hosts),
                                  key=lambda e: hostnames[e.id])
        deployment = (job.metadata_ or {}).get("deployment", {"strategy": "all"})
        paused = {"value": False}
        # A failure in an earlier run stops the rest of a resumed rollout
        already_failed = resume and any(e.status == ExecutionStatus.failed for e in every_execution)

        def waves_for(items):
            n = len(items)
            if n == 0:
                return []
            strategy = deployment.get("strategy", "all")
            if strategy == "canary":
                size = max(1, math.ceil(n * int(deployment.get("canary_percent", 10)) / 100))
                bounds = [min(size, n), n]
            elif strategy == "staged":
                bounds = sorted({math.ceil(n * 0.25), math.ceil(n * 0.5), n})
            else:
                bounds = [n]
            waves, start = [], 0
            for bound in bounds:
                bound = min(bound, n)
                if bound > start:
                    waves.append(items[start:bound])
                    start = bound
            return waves

        async def mark_skipped(items, message):
            async with AsyncSessionLocal() as skip_db:
                for item in items:
                    row = (await skip_db.execute(
                        select(PatchJobExecution).where(PatchJobExecution.id == item.id)
                    )).scalar_one()
                    row.status = ExecutionStatus.skipped
                    row.error_message = message
                    row.execution_end = datetime.utcnow()
                await skip_db.commit()

        async def run_other_waves():
            from app.services import notify
            halted = already_failed
            for index, wave in enumerate(waves_for(other_executions)):
                todo = [e for e in wave if e.id in pending_ids]
                if not todo:
                    continue
                if halted:
                    await mark_skipped(todo, "Not run: an earlier deployment wave failed")
                    continue
                # Canary finished without failures: hold the rest until someone reviews the canary results
                if (index == 1 and not resume and deployment.get("strategy") == "canary"
                        and deployment.get("pause_after_canary", True)):
                    paused["value"] = True
                    async with AsyncSessionLocal() as pause_db:
                        paused_job = (await pause_db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one()
                        paused_job.metadata_ = {**(paused_job.metadata_ or {}),
                                                "awaiting_continue": True,
                                                "awaiting_since": datetime.utcnow().isoformat()}
                        await pause_db.commit()
                    await notify.send("canary_paused", f"Canary finished, rollout paused: {job.name}",
                                      "Review the canary results in JÁRN, then continue the rollout.")
                    return
                await run_limited([e.id for e in todo], await _max_concurrent_servers(),
                                  lambda eid: execute_on_server(eid, job_id))
                async with AsyncSessionLocal() as status_db:
                    statuses = (await status_db.execute(
                        select(PatchJobExecution.status).where(PatchJobExecution.id.in_([e.id for e in todo]))
                    )).scalars().all()
                if any(st != ExecutionStatus.success for st in statuses):
                    halted = True

        async def run_kubernetes_chain():
            for position, execution in enumerate(k8s_executions):
                await execute_on_server(execution.id, job_id, kubernetes_node=True)
                async with AsyncSessionLocal() as status_db:
                    status = (await status_db.execute(
                        select(PatchJobExecution.status).where(PatchJobExecution.id == execution.id)
                    )).scalar_one()
                if status != ExecutionStatus.success:
                    async with AsyncSessionLocal() as skip_db:
                        for later in k8s_executions[position + 1:]:
                            row = (await skip_db.execute(
                                select(PatchJobExecution).where(PatchJobExecution.id == later.id)
                            )).scalar_one()
                            row.status = ExecutionStatus.skipped
                            row.error_message = f"Not run: {hostnames[execution.id]} failed its Kubernetes checks"
                            row.execution_end = datetime.utcnow()
                        await skip_db.commit()
                    break

        await asyncio.gather(run_other_waves(), run_kubernetes_chain())
        if paused["value"]:
            from app.services import audit as _audit
            await _audit.record("job.paused", category="job", resource_type="job", resource_id=job_id,
                                reason="Canary finished; rollout waiting for review",
                                details={"strategy": "canary"})
            logger.info("job_paused_after_canary", job_id=job_id)
            return

        # Update job status. populate_existing: this session loaded the executions before the servers were patched,
        # and the statuses changed in other sessions since, so the cached copies would be stale.
        result = await db.execute(
            select(PatchJobExecution).where(PatchJobExecution.job_id == job_id)
            .execution_options(populate_existing=True)
        )
        all_executions = result.scalars().all()

        failed_count = sum(1 for e in all_executions if e.status in (ExecutionStatus.failed, ExecutionStatus.rolled_back))
        rolled_back_count = sum(1 for e in all_executions if e.status == ExecutionStatus.rolled_back)
        hard_failures = sum(1 for e in all_executions if e.status == ExecutionStatus.failed)

        # rolled_back only when every problem was reverted; otherwise the run is completed with its failures listed
        job.status = JobStatus.rolled_back if rolled_back_count and not hard_failures else JobStatus.completed
        job.end_time = datetime.utcnow()
        await db.commit()

        from app.services import audit as _audit
        await _audit.record("job.complete", category="job", resource_type="job", resource_id=job_id,
                            outcome="failure" if failed_count else "success",
                            reason=f"{failed_count} server(s) failed" if failed_count else None,
                            details={"servers": len(all_executions), "failed": failed_count})
        rescan_servers.delay([str(sid) for sid in job.server_ids])
        from app.services import notify
        await notify.send(
            "job_finished",
            f"Patch job finished: {job.name}",
            f"{len(all_executions)} server(s), {failed_count} failed.",
        )

        logger.info("job_execution_completed", job_id=job_id, failed_count=failed_count)


async def execute_on_server(execution_id: str, job_id: str, kubernetes_node: bool = False):
    """Execute patches on a single server"""
    async with AsyncSessionLocal() as db:
        try:
            # Get execution
            result = await db.execute(
                select(PatchJobExecution).where(PatchJobExecution.id == execution_id)
            )
            execution = result.scalar_one_or_none()

            if not execution:
                return

            # Get server
            result = await db.execute(
                select(Server).where(Server.id == execution.server_id)
            )
            server = result.scalar_one_or_none()

            if not server:
                execution.status = ExecutionStatus.failed
                execution.error_message = "Server not found"
                await db.commit()
                return

            # Update to executing
            execution.status = ExecutionStatus.executing
            execution.execution_start = datetime.utcnow()
            await db.commit()

            # Get job for patch IDs
            result = await db.execute(
                select(PatchJob).where(PatchJob.id == job_id)
            )
            job = result.scalar_one_or_none()

            if not job:
                execution.status = ExecutionStatus.failed
                execution.error_message = "Job not found"
                await db.commit()
                return

            backup_phases = []
            backup_ref = None
            if job.backup_before:
                started = datetime.utcnow().isoformat()
                try:
                    backup_ref = await take_server_backup(server.metadata_, await _backup_config(db))
                    backup_phases.append({"name": "pre_patch_backup", "status": "success", "timestamp": started,
                                          "output": backup_ref, "error": ""})
                except BackupError as e:
                    backup_phases.append({"name": "pre_patch_backup", "status": "failed", "timestamp": started,
                                          "output": "", "error": str(e)[:500]})
                    execution.status = ExecutionStatus.failed
                    execution.error_message = f"Pre-patch backup failed; patching skipped: {e}"
                    execution.metadata_ = {"phases": backup_phases, "backup_ref": None}
                    execution.execution_end = datetime.utcnow()
                    await db.commit()
                    return

            patch_result = await execute_patches(
                server,
                job.patch_ids,
                job.dry_run,
                reboot_after=job.reboot_after,
                graceful_reboot=job.graceful_reboot,
                kubernetes_node=kubernetes_node,
                allow_removals=bool((job.metadata_ or {}).get("risks_approved")),
            )
            phases = backup_phases + patch_result.get("phases", [])

            if backup_ref and job.restore_on_failure and not patch_result["success"] and patch_result.get("needs_restore"):
                started = datetime.utcnow().isoformat()
                try:
                    await restore_server_from_backup(server.metadata_, await _backup_config(db), backup_ref)
                    phases.append({"name": "restore_from_backup", "status": "success", "timestamp": started,
                                   "output": backup_ref, "error": ""})
                    patch_result["error"] = f"{patch_result.get('error', '')} (server restored from {backup_ref})"
                except BackupError as e:
                    phases.append({"name": "restore_from_backup", "status": "failed", "timestamp": started,
                                   "output": "", "error": str(e)[:500]})
                    patch_result["error"] = f"{patch_result.get('error', '')} (restore from backup failed: {e})"

            execution.metadata_ = {"phases": phases, "backup_ref": backup_ref,
                                   "rollback": patch_result.get("rollback") if patch_result["success"] and not job.dry_run else None}

            if patch_result["success"]:
                execution.status = ExecutionStatus.success
                execution.output = patch_result.get("output", "")
            elif patch_result.get("rolled_back"):
                # The patch was reverted automatically and the server is back where it started
                execution.status = ExecutionStatus.rolled_back
                execution.error_message = patch_result.get("error", "")
                execution.output = patch_result.get("output", "")
            else:
                execution.status = ExecutionStatus.failed
                execution.error_message = patch_result.get("error", "Unknown error")
                execution.output = patch_result.get("output", "")

            execution.execution_end = datetime.utcnow()
            await db.commit()

            from app.services import audit as _audit
            phase_summary = [{"name": ph.get("name"), "status": ph.get("status")} for ph in phases]
            await _audit.record(
                "job.server_result", category="job", resource_type="server", resource_id=str(server.id),
                outcome="success" if execution.status == ExecutionStatus.success else "failure",
                reason=execution.error_message,
                details={"hostname": server.hostname, "status": execution.status.value, "phases": phase_summary,
                         "kubernetes_node": kubernetes_node},
            )
            logger.info("server_execution_completed",
                       server_id=str(server.id),
                       status=execution.status.value)

        except Exception as e:
            logger.error("server_execution_error", execution_id=execution_id, error=str(e))
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(PatchJobExecution).where(PatchJobExecution.id == execution_id)
                )
                execution = result.scalar_one_or_none()
                if execution:
                    execution.status = ExecutionStatus.failed
                    execution.error_message = str(e)
                    execution.execution_end = datetime.utcnow()
                    await db.commit()


async def _backup_config(db: AsyncSession) -> dict:
    from app.models.settings import SystemSettings

    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return dict(row.backup_config or {}) if row else {}


async def take_server_backup(metadata: Optional[dict], config: dict) -> str:
    mapping = mapping_from_server(metadata)
    if not mapping:
        raise BackupError("Server is not mapped to a Proxmox VM or container")
    provider = provider_from_config(config, mapping.get("target"))
    return await provider.backup(mapping["node"], int(mapping["vmid"]))


async def restore_server_from_backup(metadata: Optional[dict], config: dict, volid: str,
                                     target_storage: Optional[str] = None) -> None:
    mapping = mapping_from_server(metadata)
    if not mapping:
        raise BackupError("Server is not mapped to a Proxmox VM or container")
    provider = provider_from_config(config, mapping.get("target"))
    await provider.restore(mapping["node"], mapping["type"], int(mapping["vmid"]), volid, target_storage)


async def refresh_pending_count(server_id: str) -> None:
    """Rescan a server after a patch job and store the upgradable package count, or the scan error."""
    async with AsyncSessionLocal() as db:
        server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
        if not server or not server.credentials:
            return
        creds = server.credentials
        connector = SSHConnector(
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            username=creds.get("username"),
            private_key=creds.get("private_key"),
            password=creds.get("password"),
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )
        metadata = dict(server.metadata_ or {})
        try:
            if not await connector.connect():
                raise RuntimeError("SSH connection failed")
            patches = await connector.scan_available_patches()
            info = connector.scan_info or {}
            health = await connector.collect_health()
            await connector.disconnect()
            count = info.get("upgradable_packages", len(patches))
            metadata.update({
                "pending_patch_count": count,
                "scan_error": None,
                "health": {**health, "collected_at": datetime.utcnow().isoformat()},
                "scan_snapshot": build_snapshot(patches, info),
            })
            server.last_patch_scan_at = datetime.utcnow()
        except Exception as e:
            try:
                await connector.disconnect()
            except Exception:
                pass
            logger.warning("post_job_scan_failed", server_id=server_id, error=str(e))
            metadata["scan_error"] = f"Post-job scan failed: {e}"[:300]
        server.metadata_ = metadata
        await db.commit()


async def resolve_patch_packages(connector, patch_ids: List[str]) -> tuple:
    """Map bundle labels ('Bundle 1') and package names to upgradable package names."""
    scanned = await connector.scan_available_patches()
    by_name = {p.name: p for p in scanned}
    upgradable = set()
    bundle_packages = {}
    for patch in scanned:
        if patch.patch_type == "bundle" and patch.description.startswith("Packages:"):
            pkgs = [x.strip() for x in patch.description[len("Packages:"):].split(",")
                    if x.strip() and not x.strip().startswith("+")]
            bundle_packages[patch.name] = pkgs
            upgradable.update(pkgs)
        else:
            upgradable.add(patch.name)

    if ALL_UPGRADABLE in patch_ids:
        # Scheduled runs: everything pending on this server
        return sorted(upgradable), [p for p in patch_ids if p != ALL_UPGRADABLE]

    packages, unresolved = [], []
    for patch_id in patch_ids:
        if patch_id in bundle_packages:
            packages.extend(bundle_packages[patch_id])
        elif patch_id in upgradable:
            packages.append(patch_id)
        else:
            unresolved.append(patch_id)
    return sorted(set(packages)), unresolved


K8S_KUBECONFIG = "/etc/kubernetes/admin.conf"


async def _kubernetes_control_server_id():
    from app.models.settings import SystemSettings

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        return (row.general_config or {}).get("kubernetes_control_server_id") if row else None


async def _connector_for_server(server_id: str):
    async with AsyncSessionLocal() as db:
        s = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
        if not s:
            return None
        creds = dict(s.credentials or {})
        from app.services import audit as _audit
        await _audit.record("credential.use", category="credential", resource_type="server", resource_id=str(s.id),
                            details={"hostname": s.hostname, "username": creds.get("username"),
                                     "method": "key" if creds.get("private_key") else "password"})
        return SSHConnector(
            hostname=str(s.ip_address) if s.ip_address else s.hostname,
            username=creds.get("username"),
            private_key=creds.get("private_key"),
            password=creds.get("password"),
            port=int(s.port) if s.port else 22,
            host_key=pinned_host_key(s.metadata_),
        )


async def _k8s_node_name(connector) -> str:
    result = await connector.execute_command("hostname", timeout=15)
    return result.stdout.strip()


async def _k8s_drain(kube_conn, node: str):
    has_admin = await kube_conn.execute_command(f"test -f {K8S_KUBECONFIG} && echo yes", timeout=15)
    if "yes" not in has_admin.stdout:
        return False, f"No {K8S_KUBECONFIG} on the Kubernetes control host, so {node} cannot be drained"
    result = await kube_conn.execute_command(
        f"sudo kubectl --kubeconfig={K8S_KUBECONFIG} drain {node} --ignore-daemonsets --delete-emptydir-data --timeout=900s",
        timeout=1000,
    )
    return result.success, (result.stdout + result.stderr)[-500:]


async def _k8s_uncordon(kube_conn, node: str):
    result = await kube_conn.execute_command(
        f"sudo kubectl --kubeconfig={K8S_KUBECONFIG} uncordon {node}", timeout=60
    )
    return result.success, (result.stdout + result.stderr)[-300:]


async def _k8s_node_healthy(node_conn, kube_conn, node: str):
    services = await node_conn.execute_command("systemctl is-active kubelet containerd", timeout=20)
    healthz = await node_conn.execute_command("curl -s -m 5 http://127.0.0.1:10248/healthz", timeout=20)
    ready_cmd = ("sudo kubectl --kubeconfig=" + K8S_KUBECONFIG + " get node " + node +
                 " -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}'")
    ready = await kube_conn.execute_command(ready_cmd, timeout=60)
    detail = f"services={services.stdout.split()} healthz={healthz.stdout.strip()} ready={ready.stdout.strip()}"
    ok = (services.stdout.split() == ["active", "active"]
          and healthz.stdout.strip() == "ok"
          and ready.stdout.strip() == "True")
    return ok, detail


async def _k8s_wait_ready(node_conn, kube_conn, node: str, timeout: int = 900):
    waited = 0
    while True:
        ok, detail = await _k8s_node_healthy(node_conn, kube_conn, node)
        if ok:
            return True, detail
        if waited >= timeout:
            return False, detail
        await asyncio.sleep(15)
        waited += 15


async def _reconnect_when_up(connector, timeout: int = 1200) -> bool:
    waited = 0
    while waited < timeout:
        if await connector.connect():
            return True
        await asyncio.sleep(20)
        waited += 20
    return False


async def _wait_for_reboot_cycle(connector, down_timeout: int = 300) -> bool:
    """Wait for the server to drop off the network after a scheduled reboot, then for it to return."""
    waited = 0
    went_down = False
    while waited < down_timeout:
        await asyncio.sleep(15)
        waited += 15
        await connector.disconnect()
        if not await connector.connect():
            went_down = True
            break
    if not went_down:
        return False
    return await _reconnect_when_up(connector)


async def _verify_after_reboot(connector, services_before, failed_before, expect_new_kernel: bool) -> list:
    issues = []
    if expect_new_kernel:
        running = await connector.running_kernel()
        newest = await connector.newest_installed_kernel()
        if newest and running != newest:
            issues.append(f"running kernel {running} but {newest} is installed")
    now_running = set(await connector.running_services())
    missing = [svc for svc in services_before if svc not in now_running]
    if missing:
        issues.append("services not running after reboot: " + ", ".join(missing[:10]))
    new_failures = [u for u in await connector.failed_units() if u not in failed_before]
    if new_failures:
        issues.append("units failed after reboot: " + ", ".join(new_failures[:10]))
    return issues


async def execute_patches(server, patch_ids: List[str], dry_run: bool = False, reboot_after: bool = False, graceful_reboot: bool = True, kubernetes_node: bool = False, allow_removals: bool = False) -> dict:
    """Execute patches on a Linux server via SSH with safety guardrails"""
    connector = None
    kube_conn = None
    previous_versions = {}
    phases = []  # Track phase logs
    reboot_required = False

    def add_phase(name: str, status: str, output: str = "", error: str = ""):
        """Add phase log entry"""
        phases.append({
            "name": name,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "output": output[:500] if output else "",  # Limit output size
            "error": error[:500] if error else ""
        })

    try:
        # Create SSH connector for Linux servers
        # Use IP address if available, fall back to hostname (prevents DNS issues)
        connect_host = str(server.ip_address) if server.ip_address else server.hostname

        connector = SSHConnector(
            hostname=connect_host,
            username=server.credentials.get("username") if server.credentials else None,
            private_key=server.credentials.get("private_key") if server.credentials else None,
            password=server.credentials.get("password") if server.credentials else None,
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )

        # Connect to server
        # Retried because nothing on the server has changed yet
        if not await connect_with_retry(connector.connect):
            add_phase("connect", "failed", error="SSH connection failed after retries")
            return {"success": False, "error": "SSH connection failed after retries", "phases": phases}

        # Check if reboot is already pending
        logger.info("checking_reboot_status", server=server.hostname)
        reboot_status = await connector.check_reboot_required()
        if reboot_status.get("reboot_required"):
            error_msg = f"Reboot already pending: {reboot_status.get('reboot_reason', 'unknown')}"
            add_phase("pre_reboot_check", "warning", output=str(reboot_status), error=error_msg)
            logger.warning("reboot_already_pending", server=server.hostname, reason=reboot_status.get('reboot_reason'))
            if not dry_run:
                # For dry runs, we continue; for real runs, we should warn but continue (user can defer or force)
                pass

        # Pre-patch health check
        logger.info("pre_patch_health_check", server=server.hostname)
        pre_health = await connector.pre_patch_health_check()
        if not pre_health.get("overall_healthy"):
            error_msg = f"Pre-patch health check failed: {pre_health.get('errors', 'unknown issues')}"
            add_phase("pre_patch_health_check", "failed", output=str(pre_health), error=error_msg)
            logger.warning("pre_patch_health_check_failed", server=server.hostname, issues=pre_health.get("errors"))
            return {"success": False, "error": error_msg, "phases": phases}

        add_phase("pre_patch_health_check", "success", output=str(pre_health))

        from app.services.risk_analysis import detect_profiles, run_health_checks
        app_profiles = await detect_profiles(connector)
        baseline = await run_health_checks(connector, app_profiles)
        broken_before = [k for k, v in baseline.items() if not v["ok"]]
        add_phase("app_health_before", "failed" if broken_before else "success",
                  output=str({k: v["ok"] for k, v in baseline.items()})[:500],
                  error=("Unhealthy before patching: " + ", ".join(broken_before)) if broken_before else "")
        if broken_before:
            return {"success": False, "error": "Application already unhealthy before patching: " + ", ".join(broken_before),
                    "phases": phases}

        # Resolve bundle labels and package names to the packages actually upgradable on this server
        actual_packages, unresolved = await resolve_patch_packages(connector, patch_ids)
        if unresolved:
            error_msg = f"Cannot resolve to upgradable packages on this server: {', '.join(unresolved)}"
            add_phase("resolve_packages", "failed", error=error_msg)
            return {"success": False, "error": error_msg, "phases": phases}
        add_phase("resolve_packages", "success", output=", ".join(actual_packages)[:500])

        kernel_in_job = any(p.startswith("linux-image") for p in actual_packages)
        preflight = await connector.preflight_system_health()
        preflight_problems = []
        if preflight["apt_busy"]:
            preflight_problems.append("another package manager process is running")
        if preflight["dpkg_problems"]:
            preflight_problems.append("dpkg is in a broken state: " + preflight["dpkg_problems"][:200])
        if preflight["root_free_bytes"] < 1024 ** 3:
            preflight_problems.append(f"less than 1 GiB free on /")
        if preflight["var_free_bytes"] < 512 * 1024 ** 2:
            preflight_problems.append("less than 512 MiB free on /var")
        if kernel_in_job and preflight["boot_free_bytes"] < 100 * 1024 ** 2:
            preflight_problems.append("less than 100 MiB free on /boot for a new kernel")
        add_phase("preflight_system", "failed" if preflight_problems and not dry_run else "success",
                  output=str({k: v for k, v in preflight.items() if k != "dpkg_problems"})[:500],
                  error="; ".join(preflight_problems)[:500])
        if preflight_problems and not dry_run:
            return {"success": False, "error": "Preflight failed: " + "; ".join(preflight_problems), "phases": phases}
        config_files_before = set(await connector.config_file_list())

        plan = await connector.simulate_install(actual_packages)
        removals = plan["remove"]
        if removals:
            add_phase("install_plan", "warning", output=f"{len(plan['install'])} to install",
                      error=f"Would remove {len(removals)} package(s): " + ", ".join(removals)[:400])
        else:
            add_phase("install_plan", "success", output=f"{len(plan['install'])} to install, none removed")
        if removals and not allow_removals and not dry_run:
            return {"success": False, "error": f"The install would remove {len(removals)} package(s) ("
                    + ", ".join(removals[:10]) + ") and was not approved", "phases": phases}

        # Analyze patch safety
        logger.info("patch_safety_analysis", server=server.hostname, patch_count=len(actual_packages))
        safety_analysis = await connector.get_patch_safety_analysis(actual_packages)
        if safety_analysis.get("overall_safety") == "blocked":
            error_msg = f"Patch installation blocked: {safety_analysis.get('recommendations')}"
            add_phase("patch_safety_analysis", "failed", output=str(safety_analysis), error=error_msg)
            logger.error("patch_installation_blocked", server=server.hostname, reason=safety_analysis.get("recommendations"))
            return {"success": False, "error": error_msg, "phases": phases}

        add_phase("patch_safety_analysis", "success", output=str(safety_analysis))

        # Save current package versions for rollback
        logger.info("saving_package_versions", server=server.hostname, packages=actual_packages)
        previous_versions = await connector.save_package_versions(actual_packages)
        add_phase("save_versions", "success", output=str(previous_versions)[:500])

        rollback_missing = await connector.check_rollback_available(previous_versions)
        rollback_possible = not rollback_missing
        add_phase("rollback_check", "success" if rollback_possible else "warning",
                  output="" if rollback_possible else
                  "Previous versions no longer offered by any repository, so a failed patch cannot be rolled back by package: "
                  + ", ".join(rollback_missing)[:400])

        services_before = await connector.running_services()
        failed_before = await connector.failed_units()
        kernel_patch = any(p.startswith("linux-image") for p in actual_packages)

        node_name = None
        if kubernetes_node and not dry_run:
            node_name = await _k8s_node_name(connector)
            control_id = await _kubernetes_control_server_id()
            if control_id and control_id == str(server.id):
                kube_conn = connector
            elif control_id:
                kube_conn = await _connector_for_server(control_id)
                if kube_conn is None or not await kube_conn.connect():
                    add_phase("k8s_drain", "failed", error="Kubernetes control host is not reachable")
                    return {"success": False, "error": "Kubernetes control host is not reachable; node not patched", "phases": phases}
            else:
                kube_conn = connector
            drained, drain_detail = await _k8s_drain(kube_conn, node_name)
            add_phase("k8s_drain", "success" if drained else "failed", output=drain_detail if drained else "",
                      error="" if drained else drain_detail)
            if not drained:
                await _k8s_uncordon(kube_conn, node_name)
                return {"success": False, "error": f"Drain of {node_name} failed: {drain_detail}", "phases": phases}

        # Install patches
        logger.info("installing_patches", server=server.hostname, patch_count=len(actual_packages))
        result = await connector.install_patches(actual_packages, dry_run=dry_run)
        install_verified = (not result.success and not dry_run
                            and await connector.verify_packages_upgraded(actual_packages))
        if install_verified:
            logger.warning("install_reported_error_but_packages_upgraded", server=server.hostname,
                           stderr=result.stderr[-300:])

        if not (result.success or install_verified):
            add_phase("install_patches", "failed", output=result.stdout[:500], error=result.stderr[:500])
            logger.error("patch_installation_failed", server=server.hostname, stderr=result.stderr)
            # Attempt auto-rollback if installation failed
            if previous_versions and rollback_possible:
                logger.info("triggering_auto_rollback", server=server.hostname)
                rollback_result = await connector.rollback_patches(actual_packages, previous_versions)
                if rollback_result.success:
                    add_phase("auto_rollback", "success", output=rollback_result.stdout[:500])
                    logger.info("auto_rollback_succeeded", server=server.hostname)
                    return {"success": False, "rolled_back": True, "error": f"Patch installation failed and auto-rollback succeeded. Original error: {result.stderr}", "phases": phases}
                else:
                    add_phase("auto_rollback", "failed", error=rollback_result.stderr[:500])
                    logger.error("auto_rollback_failed", server=server.hostname, error=rollback_result.stderr)
                    return {"success": False, "needs_restore": True, "error": f"Patch installation failed ({result.stderr[-300:]}) and auto-rollback also failed: {rollback_result.stderr}", "phases": phases}
            return {"success": False, "needs_restore": True, "error": result.stderr, "phases": phases}

        add_phase("install_patches", "success", output=result.stdout[:500])

        if not dry_run:
            still_pending = await connector.still_upgradable(actual_packages)
            add_phase("install_verification", "failed" if still_pending else "success",
                      output="" if still_pending else "all requested packages are at their target versions",
                      error=("Still upgradable after install: " + ", ".join(still_pending))[:500] if still_pending else "")
            if still_pending:
                return {"success": False, "error": "Packages still upgradable after install: " + ", ".join(still_pending[:10]),
                        "phases": phases}
            new_config_files = sorted(set(await connector.config_file_list()) - config_files_before)
            if new_config_files:
                add_phase("config_files_review", "warning", output="",
                          error="New config files to review (package defaults kept, your versions untouched): "
                                + ", ".join(new_config_files[:20]))

        # Post-patch health check
        logger.info("post_patch_health_check", server=server.hostname)
        post_health = await connector.post_patch_health_check()
        if not post_health.get("overall_healthy"):
            error_msg = f"Post-patch health check failed: {post_health.get('errors')}"
            add_phase("post_patch_health_check", "failed", output=str(post_health), error=error_msg)
            logger.warning("post_patch_health_check_failed", server=server.hostname, issues=post_health.get("errors"))
            # Attempt auto-rollback if post-patch health check failed
            if previous_versions and rollback_possible:
                logger.info("triggering_auto_rollback_due_to_health_check", server=server.hostname)
                rollback_result = await connector.rollback_patches(actual_packages, previous_versions)
                if rollback_result.success:
                    add_phase("auto_rollback", "success", output=rollback_result.stdout[:500])
                    logger.info("auto_rollback_succeeded_after_health_check", server=server.hostname)
                    return {"success": False, "rolled_back": True, "error": f"Post-patch health check failed and auto-rollback succeeded. Health issues: {post_health.get('errors')}", "phases": phases}
                else:
                    add_phase("auto_rollback", "failed", error=rollback_result.stderr[:500])
                    logger.error("auto_rollback_failed_after_health_check", server=server.hostname, error=rollback_result.stderr)
                    return {"success": False, "needs_restore": True, "error": f"Post-patch health check failed and auto-rollback also failed: {rollback_result.stderr}", "phases": phases}
            return {"success": False, "needs_restore": True, "error": error_msg, "phases": phases}

        add_phase("post_patch_health_check", "success", output=str(post_health))

        if baseline:
            after = await run_health_checks(connector, app_profiles)
            regressions = [k for k, v in baseline.items() if v["ok"] and not after.get(k, {}).get("ok", False)]
            add_phase("app_health_after", "failed" if regressions else "success",
                      output=str({k: v["ok"] for k, v in after.items()})[:500],
                      error=("Regressed after patching: " + ", ".join(regressions)) if regressions else "")
            if regressions:
                summary = "; ".join(regressions)
                if previous_versions and rollback_possible:
                    rollback_result = await connector.rollback_patches(actual_packages, previous_versions)
                    if rollback_result.success:
                        add_phase("auto_rollback", "success", output=rollback_result.stdout[:500])
                        return {"success": False, "rolled_back": True, "error": f"Application check regressed ({summary}); packages rolled back", "phases": phases}
                    add_phase("auto_rollback", "failed", error=rollback_result.stderr[:500])
                return {"success": False, "needs_restore": True,
                        "error": f"Application check regressed ({summary}) and rollback did not recover it", "phases": phases}

        if kubernetes_node and not dry_run:
            from app.services.patch_safety import PatchSafetyChecker
            needs_reboot = bool(reboot_after and PatchSafetyChecker.get_reboot_required_patches(actual_packages))
            if needs_reboot:
                reboot_result = await connector.reboot_server(graceful=graceful_reboot)
                add_phase("reboot", "success" if reboot_result.get("success") else "failed",
                          output=str(reboot_result.get("message", "")),
                          error=str(reboot_result.get("error", "") or ""))
                if not reboot_result.get("success"):
                    return {"success": False, "error": f"Reboot of {node_name} could not be scheduled; node left cordoned", "phases": phases}
                if not await _wait_for_reboot_cycle(connector):
                    return {"success": False, "needs_restore": True,
                            "error": f"{node_name} did not reboot and come back; node left cordoned", "phases": phases}
                issues = await _verify_after_reboot(connector, services_before, failed_before, kernel_patch)
                add_phase("post_reboot_verification", "failed" if issues else "success",
                          output="" if issues else "kernel, services and units verified",
                          error="; ".join(issues)[:500])
                if issues:
                    return {"success": False, "needs_restore": True,
                            "error": f"{node_name} failed post-reboot verification: " + "; ".join(issues),
                            "phases": phases}
            ready, ready_detail = await _k8s_wait_ready(connector, kube_conn, node_name)
            add_phase("k8s_ready", "success" if ready else "failed", output=ready_detail,
                      error="" if ready else ready_detail)
            if not ready:
                return {"success": False, "error": f"{node_name} not healthy after patching ({ready_detail}); node left cordoned", "phases": phases}
            uncordoned, uncordon_detail = await _k8s_uncordon(kube_conn, node_name)
            add_phase("k8s_uncordon", "success" if uncordoned else "failed", output=uncordon_detail,
                      error="" if uncordoned else uncordon_detail)
            if not uncordoned:
                return {"success": False, "error": f"{node_name} is healthy but could not be uncordoned: {uncordon_detail}", "phases": phases}

        # Check if reboot is needed and requested
        if reboot_after and not dry_run and not kubernetes_node:
            logger.info("checking_reboot_requirement", server=server.hostname, patches=actual_packages)
            from app.services.patch_safety import PatchSafetyChecker
            reboot_required_patches = PatchSafetyChecker.get_reboot_required_patches(actual_packages)

            if reboot_required_patches:
                logger.info("initiating_reboot", server=server.hostname, patches=reboot_required_patches)
                reboot_result = await connector.reboot_server(graceful=graceful_reboot)

                if reboot_result.get("success"):
                    add_phase("reboot", "success", output=f"Reboot initiated. Graceful: {graceful_reboot}")
                    logger.info("reboot_scheduled", server=server.hostname, graceful=graceful_reboot)
                    if not await _wait_for_reboot_cycle(connector):
                        return {"success": False, "needs_restore": True,
                                "error": f"{server.hostname} did not reboot and come back", "phases": phases}
                    issues = await _verify_after_reboot(connector, services_before, failed_before, kernel_patch)
                    add_phase("post_reboot_verification", "failed" if issues else "success",
                              output="" if issues else "kernel, services and units verified",
                              error="; ".join(issues)[:500])
                    if issues:
                        return {"success": False, "needs_restore": True,
                                "error": "Post-reboot verification failed: " + "; ".join(issues), "phases": phases}
                else:
                    add_phase("reboot", "failed", error=reboot_result.get("error", "Failed to schedule reboot"))
                    logger.error("reboot_failed", server=server.hostname, error=reboot_result.get("error"))

        logger.info("patch_execution_succeeded", server=server.hostname)
        await connector.disconnect()

        return {
            "success": True,
            "output": result.stdout,
            "error": "",
            "phases": phases,
            "reboot_required": reboot_required,
            "rollback": {
                "packages": actual_packages,
                "previous_versions": previous_versions,
                "rollback_possible": rollback_possible,
            },
        }

    except Exception as e:
        logger.error("patch_execution_error", server=server.hostname, error=str(e))
        # Attempt emergency rollback
        if connector and previous_versions:
            try:
                logger.info("emergency_rollback", server=server.hostname)
                if await connector.connect():
                    rollback_result = await connector.rollback_patches(list(previous_versions.keys()), previous_versions)
                    if rollback_result.success:
                        logger.info("emergency_rollback_succeeded", server=server.hostname)
                    else:
                        logger.error("emergency_rollback_failed", server=server.hostname)
            except Exception as rollback_error:
                logger.error("emergency_rollback_error", server=server.hostname, error=str(rollback_error))
        installed = any(p["name"] == "install_patches" for p in phases)
        return {"success": False, "needs_restore": installed, "error": str(e), "phases": phases}

    finally:
        if kube_conn is not None and kube_conn is not connector:
            try:
                await kube_conn.disconnect()
            except Exception:
                pass
        if connector:
            try:
                await connector.disconnect()
            except Exception as e:
                logger.warning("disconnect_error", server=server.hostname, error=str(e))


async def _max_concurrent_servers() -> int:
    from app.models.settings import SystemSettings
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return concurrency_limit((row.patching_config or {}).get("max_concurrent_servers") if row else None)


async def _job_rollback_items(job_id: str) -> tuple:
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one_or_none()
        if job is None:
            return None, []
        rows = (await db.execute(select(PatchJobExecution).where(PatchJobExecution.job_id == job_id))).scalars().all()
        items = []
        for e in rows:
            srv = (await db.execute(select(Server).where(Server.id == e.server_id))).scalar_one_or_none()
            items.append({"execution_id": str(e.id), "server_id": str(e.server_id),
                          "hostname": srv.hostname if srv else str(e.server_id), "status": e.status.value,
                          "metadata": dict(e.metadata_ or {}), "dry_run": bool(job.dry_run)})
        return job, items


async def async_rollback_job(job_id: str) -> dict:
    """Downgrades the packages each successfully patched server changed, back to the versions recorded before the run."""
    from app.services import audit, notify
    job, items = await _job_rollback_items(job_id)
    if job is None:
        return {"error": "Job not found"}
    plan = plan_job_rollback(items)
    await audit.record("job.rollback.start", category="job", resource_type="job", resource_id=job_id,
                       details={"servers": [i["hostname"] for i in plan["eligible"]]})

    results: Dict[str, Dict[str, Any]] = {}

    async def roll_back(item: Dict[str, Any]) -> None:
        outcome: Dict[str, Any] = {"ok": False, "packages": item["packages"], "at": datetime.utcnow().isoformat()}
        connector = await _connector_for_server(item["server_id"])
        try:
            if connector is None or not await connector.connect():
                outcome["error"] = "Could not connect to the server"
            else:
                result = await connector.rollback_patches(item["packages"], item["previous_versions"])
                outcome["ok"] = bool(result.success)
                if not result.success:
                    outcome["error"] = (result.stderr or "rollback command failed")[:500]
        except Exception as e:
            outcome["error"] = str(e)[:500]
        finally:
            if connector is not None:
                try:
                    await connector.disconnect()
                except Exception:
                    pass
        results[item["execution_id"]] = outcome
        async with AsyncSessionLocal() as db:
            execution = (await db.execute(select(PatchJobExecution).where(
                PatchJobExecution.id == item["execution_id"]))).scalar_one()
            execution.metadata_ = {**dict(execution.metadata_ or {}), "manual_rollback": outcome}
            if outcome["ok"]:
                execution.status = ExecutionStatus.rolled_back
            await db.commit()
        await audit.record("job.rollback.server", category="job", resource_type="server",
                           resource_id=item["server_id"], outcome="success" if outcome["ok"] else "failure",
                           reason=outcome.get("error"), details={"hostname": item["hostname"], "packages": item["packages"]})

    await run_limited(plan["eligible"], await _max_concurrent_servers(), roll_back)

    failed = [i["hostname"] for i in plan["eligible"] if not results.get(i["execution_id"], {}).get("ok")]
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one()
        if not failed and plan["eligible"]:
            job.status = JobStatus.rolled_back
        job.metadata_ = {**dict(job.metadata_ or {}), "manual_rollback": {
            "at": datetime.utcnow().isoformat(), "rolled_back": len(plan["eligible"]) - len(failed),
            "failed": failed, "skipped": [{"hostname": i["hostname"], "reason": i["reason"]} for i in plan["skipped"]]}}
        await db.commit()
    await audit.record("job.rollback.complete", category="job", resource_type="job", resource_id=job_id,
                       outcome="failure" if failed else "success",
                       reason=f"{len(failed)} server(s) could not be rolled back" if failed else None,
                       details={"rolled_back": len(plan["eligible"]) - len(failed), "failed": failed})
    await notify.send("job_rollback", f"Rollback finished: {job.name}",
                      f"{len(plan['eligible']) - len(failed)} rolled back, {len(failed)} failed.")
    return {"rolled_back": len(plan["eligible"]) - len(failed), "failed": failed}


@app.task(bind=True, max_retries=0)
def rollback_job(self, job_id: str):
    return asyncio.run(async_rollback_job(job_id))


@app.task(bind=True, max_retries=0)
def execute_distro_upgrade(self, server_id: str, target_version: str, acknowledge_risks: bool, job_id: str, execution_id: str):
    args = (server_id, target_version, acknowledge_risks, job_id, execution_id)
    try:
        try:
            asyncio.run(async_execute_distro_upgrade(*args))
        except RuntimeError as e:
            if "attached to a different loop" in str(e):
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(asyncio.run, async_execute_distro_upgrade(*args)).result()
            else:
                raise
    except Exception as exc:
        logger.error("distro_upgrade_task_error", server_id=server_id, error=str(exc), exc_info=True)
        asyncio.run(_mark_distro_job_failed(job_id, execution_id, str(exc)))
        raise


async def _mark_distro_job_failed(job_id: str, execution_id: str, error: str):
    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one_or_none()
        execution = (await db.execute(select(PatchJobExecution).where(PatchJobExecution.id == execution_id))).scalar_one_or_none()
        if job and job.status in (JobStatus.pending, JobStatus.running):
            job.status = JobStatus.failed
            job.end_time = datetime.utcnow()
        if execution and execution.status in (ExecutionStatus.pending, ExecutionStatus.executing):
            execution.status = ExecutionStatus.failed
            execution.error_message = error
            execution.execution_end = datetime.utcnow()
        await db.commit()


async def async_execute_distro_upgrade(server_id: str, target_version: str, acknowledge_risks: bool, job_id: str, execution_id: str) -> dict:
    from app.services.distro_upgrade import DistroUpgradeChecker, UpgradeStatus

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Server).where(Server.id == server_id))
        server = result.scalar_one_or_none()
        if not server:
            logger.error("distro_upgrade_server_not_found", server_id=server_id)
            return {"success": False, "error": "Server not found"}
        server_info = {
            "hostname": server.hostname,
            "ip": str(server.ip_address) if server.ip_address else None,
            "os_type": server.os_type.value,
            "os_version": server.os_version,
            "port": int(server.port) if server.port else 22,
            "credentials": dict(server.credentials or {}),
            "metadata": dict(server.metadata_ or {}),
        }

    creds = server_info["credentials"]
    connector = SSHConnector(
        hostname=server_info["ip"] or server_info["hostname"],
        username=creds.get("username"),
        private_key=creds.get("private_key"),
        password=creds.get("password"),
        port=server_info["port"],
        host_key=pinned_host_key(server_info["metadata"]),
    )
    phases = []
    backup_dir = None
    backup_ref = None

    async with AsyncSessionLocal() as db:
        job = (await db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one()
        execution = (await db.execute(select(PatchJobExecution).where(PatchJobExecution.id == execution_id))).scalar_one()
        job.status = JobStatus.running
        job.start_time = datetime.utcnow()
        execution.status = ExecutionStatus.executing
        execution.execution_start = datetime.utcnow()
        await db.commit()

    def add_phase(name: str, status: str, output: str = "", error: str = ""):
        phases.append({
            "name": name,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "output": output[-1500:] if output else "",
            "error": error[-1500:] if error else "",
        })

    async def persist():
        async with AsyncSessionLocal() as db:
            execution = (await db.execute(select(PatchJobExecution).where(PatchJobExecution.id == execution_id))).scalar_one()
            execution.metadata_ = {"phases": list(phases), "backup_dir": backup_dir, "backup_ref": backup_ref}
            await db.commit()

    async def run_step(name: str, command: str, timeout: int = 900) -> bool:
        res = await connector.execute_command(command, timeout=timeout)
        if res.success:
            add_phase(name, "success", output=res.stdout)
        else:
            add_phase(name, "failed", output=res.stdout, error=res.stderr or "command failed")
        await persist()
        return res.success

    async def finish(success: bool, error: str = "") -> dict:
        await connector.disconnect()
        await refresh_pending_count(server_id)
        async with AsyncSessionLocal() as db:
            job = (await db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one()
            execution = (await db.execute(select(PatchJobExecution).where(PatchJobExecution.id == execution_id))).scalar_one()
            execution.metadata_ = {"phases": list(phases), "backup_dir": backup_dir, "backup_ref": backup_ref}
            execution.status = ExecutionStatus.success if success else ExecutionStatus.failed
            execution.output = f"Backup: {backup_dir}" if backup_dir else ""
            execution.error_message = error or None
            execution.execution_end = datetime.utcnow()
            job.status = JobStatus.completed if success else JobStatus.failed
            job.end_time = datetime.utcnow()
            await db.commit()
        from app.services import notify
        await notify.send(
            "distro_upgrade_finished",
            f"Distro upgrade {'finished' if success else 'FAILED'}: {server_info['hostname']}",
            error or f"Upgraded to {target_version}.",
        )
        logger.info("distro_upgrade_finished", hostname=server_info["hostname"],
                    success=success, error=error, backup_dir=backup_dir)
        return {"success": success, "error": error, "phases": phases, "backup_dir": backup_dir}

    if not await connector.connect():
        add_phase("connect", "failed", error="SSH connection failed")
        return await finish(False, "SSH connection failed")

    os_type = server_info["os_type"]
    detection = await connector.detect_critical_applications()
    analysis = await DistroUpgradeChecker.analyze_upgrade_readiness(
        connector, os_type, server_info["os_version"], target_version,
        detection.get("applications", []),
    )
    status = analysis["upgrade_status"]
    add_phase("readiness_check", status.value if hasattr(status, "value") else str(status),
              output="; ".join(analysis.get("warnings", []) + analysis.get("recommendations", [])),
              error="; ".join(analysis.get("blockers", [])))
    if status in (UpgradeStatus.BLOCKED, UpgradeStatus.NOT_SUPPORTED):
        return await finish(False, "Readiness check blocked the upgrade")
    if status == UpgradeStatus.RISKY and not acknowledge_risks:
        return await finish(False, "Readiness check reported risks; acknowledge them to proceed")

    started = datetime.utcnow().isoformat()
    try:
        async with AsyncSessionLocal() as db:
            config = await _backup_config(db)
        backup_ref = await take_server_backup(server_info["metadata"], config)
        phases.append({"name": "pre_upgrade_backup", "status": "success", "timestamp": started,
                       "output": backup_ref, "error": ""})
    except BackupError as e:
        phases.append({"name": "pre_upgrade_backup", "status": "failed", "timestamp": started,
                       "output": "", "error": str(e)[:500]})
        await persist()
        return await finish(False, f"A backup is required before a distro upgrade and it failed: {e}")
    await persist()

    current_codename = None
    res = await connector.execute_command(". /etc/os-release && echo $VERSION_CODENAME", timeout=10)
    if res.success:
        current_codename = res.stdout.strip()
    target_codename = DistroUpgradeChecker.codename_for(os_type, target_version)
    if os_type == "debian" and (not current_codename or not target_codename):
        return await finish(False, "Could not determine current or target Debian codename")
    if os_type == "debian" and current_codename == target_codename:
        return await finish(False, "Server already runs the target release")

    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_dir = f"/root/pre-upgrade-{stamp}"
    if not await run_step("backup", f"sudo sh -c 'mkdir -p {backup_dir} && cp -a /etc/apt {backup_dir}/ && dpkg --get-selections > {backup_dir}/packages.txt'", timeout=600):
        return await finish(False, "Backup failed; upgrade not started")

    if os_type == "debian":
        repo_sed = f"sed -i 's/\\b{current_codename}\\b/{target_codename}/g'"
        if not await run_step("switch_repositories",
                              f"sudo {repo_sed} /etc/apt/sources.list && sudo find /etc/apt/sources.list.d \\( -name '*.list' -o -name '*.sources' \\) -exec {repo_sed} {{}} +",
                              timeout=60):
            return await finish(False, "Failed to switch repositories")
        if not await run_step("apt_update", "sudo apt-get update", timeout=900):
            return await finish(False, "apt-get update failed")
        upgrade_cmd = ("sudo env DEBIAN_FRONTEND=noninteractive apt-get -y "
                       "-o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold full-upgrade")
        if not await run_step("distro_upgrade", upgrade_cmd, timeout=7200):
            return await finish(False, "Distribution upgrade failed; see backup at " + backup_dir)
    elif os_type == "ubuntu":
        if not await run_step("install_upgrader", "sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y update-manager-core", timeout=900):
            return await finish(False, "Could not install update-manager-core")
        if not await run_step("distro_upgrade",
                              "sudo env DEBIAN_FRONTEND=noninteractive do-release-upgrade -f DistUpgradeViewNonInteractive",
                              timeout=7200):
            return await finish(False, "Release upgrade failed; see backup at " + backup_dir)
    else:
        return await finish(False, f"Distro upgrades are not supported for {os_type}")

    await run_step("autoremove", "sudo apt-get -y autoremove", timeout=900)

    res = await connector.execute_command("cat /etc/os-release", timeout=10)
    running_version = None
    for line in res.stdout.splitlines():
        if line.startswith("VERSION_ID="):
            running_version = line.split("=", 1)[1].strip('"')
    if not running_version or not running_version.startswith(target_version):
        add_phase("verify_release", "failed", output=res.stdout, error=f"Expected {target_version}, found {running_version}")
        return await finish(False, "Post-upgrade release verification failed; reboot not scheduled")
    add_phase("verify_release", "success", output=f"VERSION_ID={running_version}")

    reboot = await connector.reboot_server(graceful=True)
    add_phase("reboot", "success" if reboot.get("success") else "failed",
              output=reboot.get("message", ""), error=reboot.get("error", "") or "")
    return await finish(bool(reboot.get("success")), "" if reboot.get("success") else "Reboot could not be scheduled")


@app.task(bind=True, max_retries=0)
def discover_servers(self, targets: str, port: int, username: str, auth_method: str,
                     password: Optional[str] = None, private_key: Optional[str] = None,
                     approved: Optional[dict] = None):
    args = (targets, port, username, auth_method, password, private_key, approved)
    try:
        return asyncio.run(async_discover_servers(*args))
    except RuntimeError as e:
        if "attached to a different loop" in str(e):
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(asyncio.run, async_discover_servers(*args)).result()
        raise


async def async_discover_servers(targets: str, port: int, username: str, auth_method: str,
                                 password: Optional[str], private_key: Optional[str],
                                 approved: Optional[dict] = None) -> dict:
    from app.models.server import OSType, AuthMethod, ConnectivityStatus
    from app.services.bulk_discovery import parse_targets, run_discovery

    addresses = parse_targets(targets)
    results = await run_discovery(addresses, port, username, password, private_key, approved)

    credentials = {"username": username}
    if password:
        credentials["password"] = password
    if private_key:
        credentials["private_key"] = private_key

    added, skipped = [], []
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Server.ip_address, Server.hostname))).all()
        existing_ips = {str(ip) for ip, _ in rows if ip}
        existing_names = {name for _, name in rows}

        for r in results:
            if r["status"] != "linux":
                skipped.append({"ip": r["ip"], "reason": r.get("detail") or r["status"],
                                "status": r["status"], "host_key_fingerprint": r.get("host_key_fingerprint")})
                continue
            if r["ip"] in existing_ips:
                skipped.append({"ip": r["ip"], "reason": "already in inventory"})
                continue

            hostname = r["hostname"] if r["hostname"] not in existing_names else r["ip"]
            db.add(Server(
                hostname=hostname,
                ip_address=r["ip"],
                os_type=OSType(r["os_type"]),
                os_version=r["os_version"],
                auth_method=AuthMethod.ssh_key if private_key else AuthMethod.ssh_password,
                port=str(port),
                credentials=credentials,
                metadata_={"ssh_host_key": r["host_key"]},
                connectivity_status=ConnectivityStatus.healthy,
                last_check_at=datetime.utcnow(),
            ))
            existing_ips.add(r["ip"])
            existing_names.add(hostname)
            added.append({"ip": r["ip"], "hostname": hostname,
                          "os_type": r["os_type"], "os_version": r["os_version"]})
        await db.commit()

    async with AsyncSessionLocal() as db:
        new_ids = (await db.execute(
            select(Server.id).where(Server.ip_address.in_([a["ip"] for a in added]))
        )).scalars().all() if added else []
    for sid in new_ids:
        collect_inventory_task.delay(str(sid))

    logger.info("bulk_discovery_finished", total=len(addresses), added=len(added), skipped=len(skipped))
    return {"total": len(addresses), "added": added, "skipped": skipped}


@app.task(bind=True, max_retries=0)
def run_compliance_assessment(self, assessment_id: str):
    try:
        try:
            asyncio.run(async_run_compliance_assessment(assessment_id))
        except RuntimeError as e:
            if "attached to a different loop" in str(e):
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(asyncio.run, async_run_compliance_assessment(assessment_id)).result()
            else:
                raise
    except Exception as exc:
        logger.error("compliance_assessment_error", assessment_id=assessment_id, error=str(exc), exc_info=True)
        asyncio.run(_mark_assessment_failed(assessment_id, str(exc)))
        raise


async def _mark_assessment_failed(assessment_id: str, error: str):
    from app.models.compliance import ComplianceAssessment

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ComplianceAssessment).where(ComplianceAssessment.id == assessment_id))).scalar_one_or_none()
        if row:
            row.status = "failed"
            row.error = error
            row.completed_at = datetime.utcnow()
            await db.commit()


async def async_run_compliance_assessment(assessment_id: str):
    from app.models.compliance import ComplianceAssessment
    from app.services import compliance as cz

    servers = []
    async with AsyncSessionLocal() as db:
        for s in (await db.execute(select(Server))).scalars().all():
            executions = (await db.execute(
                select(PatchJobExecution).where(PatchJobExecution.server_id == s.id)
                .order_by(PatchJobExecution.created_at.desc())
            )).scalars().all()
            successes = [e.execution_end for e in executions
                         if e.status == ExecutionStatus.success and e.execution_end]
            servers.append({
                "id": str(s.id),
                "hostname": s.hostname,
                "ip": str(s.ip_address) if s.ip_address else None,
                "port": int(s.port) if s.port else 22,
                "creds": dict(s.credentials or {}),
                "host_key": pinned_host_key(s.metadata_),
                "db": {
                    "last_successful_patch": max(successes) if successes else None,
                    "latest_execution_status": executions[0].status.value if executions else None,
                },
            })

    semaphore = asyncio.Semaphore(4)

    async def assess(info: dict) -> dict:
        base = {"id": info["id"], "hostname": info["hostname"], "ip": info["ip"]}
        creds = info["creds"]
        if not creds.get("username"):
            return {**base, "status": "no_credentials", "detail": "No SSH credentials stored"}

        async with semaphore:
            connector = SSHConnector(
                hostname=info["ip"] or info["hostname"],
                username=creds.get("username"),
                private_key=creds.get("private_key"),
                password=creds.get("password"),
                port=info["port"],
                host_key=info.get("host_key"),
            )
            try:
                if not await connector.connect():
                    return {**base, "status": "unreachable", "detail": "SSH connection failed"}
                facts = {}
                for key, command in cz.COMMANDS.items():
                    res = await connector.execute_command(command, timeout=60)
                    facts[key] = res.stdout or ""
            except Exception as e:
                return {**base, "status": "error", "detail": str(e)}
            finally:
                try:
                    await connector.disconnect()
                except Exception:
                    pass

        checks = cz.evaluate_host(facts, info["db"])
        os_name = cz._parse_os_release(facts.get("os_release")).get("PRETTY_NAME", "unknown OS")
        return {**base, "status": "assessed", "detail": os_name,
                "checks": checks, "frameworks": cz.score_host(checks)}

    hosts = await asyncio.gather(*(assess(info) for info in servers))
    hosts = sorted(hosts, key=lambda h: h["hostname"])
    results = cz.build_results(hosts)

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(ComplianceAssessment).where(ComplianceAssessment.id == assessment_id))).scalar_one()
        row.results = results
        row.status = "completed"
        row.completed_at = datetime.utcnow()
        await db.commit()

    logger.info("compliance_assessment_completed", assessment_id=assessment_id,
                assessed=results["assessed_hosts"], total=results["total_hosts"])


@app.task(bind=True, max_retries=0)
def run_manual_backup(self, server_id: str):
    return asyncio.run(async_manual_backup(server_id))


@app.task(bind=True, max_retries=0)
def run_manual_restore(self, server_id: str, volid: str, target_storage: Optional[str] = None):
    return asyncio.run(async_manual_restore(server_id, volid, target_storage))


async def async_manual_backup(server_id: str) -> dict:
    async with AsyncSessionLocal() as db:
        server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one()
        metadata = dict(server.metadata_ or {})
        config = await _backup_config(db)
    volid = await take_server_backup(metadata, config)
    async with AsyncSessionLocal() as db:
        server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one()
        server.metadata_ = {**(server.metadata_ or {}),
                            "last_backup": {"volid": volid, "created_at": datetime.utcnow().isoformat()}}
        await db.commit()
    logger.info("manual_backup_completed", server_id=server_id, volid=volid)
    return {"volid": volid}


async def async_manual_restore(server_id: str, volid: str, target_storage: Optional[str] = None) -> dict:
    async with AsyncSessionLocal() as db:
        server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one()
        metadata = dict(server.metadata_ or {})
        config = await _backup_config(db)
    try:
        await restore_server_from_backup(metadata, config, volid, target_storage)
    except Exception as e:
        from app.services import notify
        await notify.send("restore_failed", f"Restore failed: {metadata.get('backup', {}).get('target', 'server')}",
                          str(e))
        raise
    logger.info("manual_restore_completed", server_id=server_id, volid=volid)
    return {"restored": volid}


async def async_offsite_backup(manual: bool = False) -> dict:
    from app.models.settings import SystemSettings
    from app.services import audit, notify, offsite
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        cfg = dict(row.offsite_config or {}) if row else {}
    if not cfg.get("enabled"):
        return {"skipped": "The off-host copy is turned off"}
    await audit.record("offsite.start", category="backup", source="worker",
                       details={"host": cfg.get("host"), "manual": manual})
    try:
        result = await asyncio.to_thread(offsite.run, cfg)
    except Exception as e:
        await audit.record("offsite.complete", category="backup", outcome="failure", source="worker",
                           reason=str(e), details={"host": cfg.get("host")})
        await notify.send("offsite_backup_failed", "Off-host copy failed", str(e))
        await _store_offsite_last_run(False, str(e))
        raise
    for name in result["uploaded"]:
        await audit.record("offsite.upload", category="backup", source="worker", resource_type="file",
                           resource_id=name, details={"host": cfg.get("host")})
    for name in result["removed"]:
        await audit.record("offsite.prune", category="backup", source="worker", resource_type="file",
                           resource_id=name, details={"host": cfg.get("host"), "keep_copies": result["keep_copies"]})
    await audit.record("offsite.complete", category="backup", source="worker",
                       details={"host": cfg.get("host"), "uploaded": result["uploaded"], "removed": result["removed"]})
    await _store_offsite_last_run(True, f"{len(result['uploaded'])} uploaded, {len(result['removed'])} removed")
    logger.info("offsite_completed", uploaded=len(result["uploaded"]), removed=len(result["removed"]))
    return result


async def _store_offsite_last_run(ok: bool, summary: str) -> None:
    from datetime import datetime as _dt
    from app.models.settings import SystemSettings
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        if row is None:
            return
        row.offsite_config = {**dict(row.offsite_config or {}),
                              "last_run": {"at": _dt.utcnow().isoformat(), "ok": ok, "summary": summary[:500]}}
        await db.commit()


@app.task(bind=True, max_retries=0)
def run_offsite_backup(self, manual: bool = False):
    return asyncio.run(async_offsite_backup(manual))


@app.task(bind=True, max_retries=0)
def rescan_stale_servers(self, force: bool = False):
    return asyncio.run(async_rescan_stale_servers(force))


@app.task(bind=True, max_retries=0)
def rescan_servers(self, server_ids: list):
    return asyncio.run(_rescan_list(server_ids))


async def _rescan_list(server_ids: list) -> dict:
    semaphore = asyncio.Semaphore(4)

    async def scan(sid: str):
        async with semaphore:
            await refresh_pending_count(sid)

    await asyncio.gather(*(scan(sid) for sid in server_ids))
    return {"rescanned": len(server_ids)}


async def async_rescan_stale_servers(force: bool) -> dict:
    from datetime import timedelta
    from sqlalchemy import or_
    from app.models.settings import SystemSettings

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        hours = int((row.general_config or {}).get("scan_interval_hours", 6)) if row else 6
        if hours == 0 and not force:
            return {"rescanned": 0, "skipped": "scheduled scans are disabled"}
        query = select(Server.id)
        if not force:
            cutoff = datetime.utcnow() - timedelta(hours=hours)
            query = query.where(or_(Server.last_patch_scan_at.is_(None), Server.last_patch_scan_at < cutoff))
        server_ids = [str(sid) for sid in (await db.execute(query)).scalars().all()]

    semaphore = asyncio.Semaphore(4)

    async def scan(sid: str):
        async with semaphore:
            await refresh_pending_count(sid)

    await asyncio.gather(*(scan(sid) for sid in server_ids))
    logger.info("rescan_completed", servers=len(server_ids), forced=force)
    return {"rescanned": len(server_ids)}


@app.task(bind=True, max_retries=0)
def collect_inventory_task(self, server_id: str):
    return asyncio.run(async_collect_inventory(server_id))


async def async_collect_inventory(server_id: str) -> dict:
    from app.services.inventory import collect_inventory

    connector = await _connector_for_server(server_id)
    if connector is None:
        return {"error": "Server not found"}
    inventory: dict
    if not await connector.connect():
        inventory = {"error": "SSH connection failed", "collected_at": datetime.utcnow().isoformat()}
    else:
        try:
            inventory = await collect_inventory(connector)
        except Exception as e:
            inventory = {"error": str(e), "collected_at": datetime.utcnow().isoformat()}
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass

    async with AsyncSessionLocal() as db:
        server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
        if server:
            server.metadata_ = {**(server.metadata_ or {}), "inventory": inventory}
            await db.commit()
    logger.info("inventory_collected", server_id=server_id, error=inventory.get("error"))
    return {"server_id": server_id, "error": inventory.get("error")}


async def async_check_certificate_expiry() -> dict:
    from app.services import tls
    from app.services import notify
    async with AsyncSessionLocal() as db:
        settings = await tls.read_settings_for_metrics(db)
    material = tls.active_material(settings or {})
    if not material:
        return {"checked": False, "reason": "HTTP mode, no certificate to check"}
    days_left = tls.summarize(material["cert_pem"])["days_left"]
    if days_left < 30:
        from app.services import audit as _audit
        await _audit.record('certificate.expiring', category='system', source='scheduler', outcome='info', details={'days_left': days_left})
        await notify.send("certificate_expiring", f"Web certificate expires in {days_left} days",
                          "Renew it in Admin Settings > Web access, or generate a new self-signed certificate.")
    return {"checked": True, "days_left": days_left}


@app.task(bind=True, max_retries=0)
def check_certificate_expiry(self):
    return asyncio.run(async_check_certificate_expiry())


@app.task(bind=True, max_retries=0)
def run_due_schedules(self):
    from app.services.schedule_runner import run_due
    return asyncio.run(run_due())


# The task time limit is 180 minutes, so a job still marked running after this long has lost its worker
STUCK_JOB_AFTER_MINUTES = 200


async def async_fail_stuck_jobs() -> dict:
    from datetime import timedelta
    from app.services import notify
    cutoff = datetime.utcnow() - timedelta(minutes=STUCK_JOB_AFTER_MINUTES)
    failed_jobs = []
    async with AsyncSessionLocal() as db:
        jobs = (await db.execute(
            select(PatchJob).where(PatchJob.status == JobStatus.running, PatchJob.start_time < cutoff)
        )).scalars().all()
        for job in jobs:
            metadata = dict(job.metadata_ or {})
            if metadata.get("awaiting_continue"):
                continue  # Paused on purpose for a canary review
            executions = (await db.execute(
                select(PatchJobExecution).where(PatchJobExecution.job_id == job.id)
            )).scalars().all()
            now = datetime.utcnow()
            for execution in executions:
                if execution.status in (ExecutionStatus.pending, ExecutionStatus.executing):
                    execution.status = ExecutionStatus.failed
                    execution.error_message = "Stopped by the watchdog: the worker stopped before this server finished"
                    execution.execution_end = now
            job.status = JobStatus.failed
            job.end_time = now
            metadata["watchdog"] = {"stopped_at": now.isoformat(), "reason": "worker stopped"}
            job.metadata_ = metadata
            failed_jobs.append(job.name)
            from app.services import audit as _audit
            await _audit.record('job.watchdog', category='job', source='scheduler', outcome='failure', resource_type='job',
                                resource_id=str(job.id), correlation_id=str(job.id), reason='worker stopped before the job finished',
                                details={'job': job.name})
        await db.commit()
    for name in failed_jobs:
        await notify.send("job_stuck", f"Patch job stopped by watchdog: {name}",
                          "The worker stopped before the job finished. Check the job and retry the failed servers.")
    if failed_jobs:
        logger.warning("stuck_jobs_failed", jobs=failed_jobs)
    return {"failed": failed_jobs}


@app.task(bind=True, max_retries=0)
def fail_stuck_jobs(self):
    return asyncio.run(async_fail_stuck_jobs())


async def async_scheduled_compliance() -> dict:
    from app.models.compliance import ComplianceAssessment
    async with AsyncSessionLocal() as db:
        row = ComplianceAssessment(framework="all", status="running")
        db.add(row)
        await db.commit()
        await db.refresh(row)
        assessment_id = str(row.id)
    run_compliance_assessment.delay(assessment_id)
    from app.services import audit as _audit
    await _audit.record('compliance.scheduled', category='compliance', source='scheduler', resource_type='assessment',
                        resource_id=assessment_id, details={'framework': 'all'})
    logger.info("scheduled_compliance_started", assessment_id=assessment_id)
    return {"assessment_id": assessment_id}


@app.task(bind=True, max_retries=0)
def scheduled_compliance(self):
    return asyncio.run(async_scheduled_compliance())


@app.task(bind=True, max_retries=0)
def evaluate_alerts(self):
    from app.services.monitoring import evaluate_and_notify
    return asyncio.run(evaluate_and_notify())


async def async_archive_audit() -> dict:
    """Archive audit events older than the retention period to a compressed file, then remove them.
    The archive keeps each event's hash, and an 'audit.archived' event records the last hash that was removed."""
    import gzip
    import json as _json
    import os as _os
    from datetime import timedelta
    from sqlalchemy import select as _select, delete as _delete
    from app.models.audit import AuditLog
    from app.services import audit as _audit
    days = int(_os.environ.get("AUDIT_RETENTION_DAYS", "365"))
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            _select(AuditLog).where(AuditLog.created_at < cutoff).order_by(AuditLog.seq).limit(100000)
        )).scalars().all()
        if not rows:
            return {"archived": 0, "retention_days": days}
        archive_dir = _os.path.join("/backups", "audit")
        _os.makedirs(archive_dir, exist_ok=True)
        name = f"audit-{rows[0].seq}-{rows[-1].seq}.jsonl.gz"
        path = _os.path.join(archive_dir, name)
        with gzip.open(path, "wt", encoding="utf-8") as f:
            for r in rows:
                f.write(_json.dumps({
                    "seq": r.seq, "created_at": r.created_at.isoformat(), "category": r.category,
                    "action": r.action, "outcome": r.outcome, "source": r.source, "actor_name": r.actor_name,
                    "actor_id": str(r.actor_id) if r.actor_id else None, "resource_type": r.resource_type,
                    "resource_id": str(r.resource_id) if r.resource_id else None,
                    "correlation_id": r.correlation_id, "ip_address": str(r.ip_address) if r.ip_address else None,
                    "reason": r.reason, "details": r.details, "prev_hash": r.prev_hash, "row_hash": r.row_hash,
                }, default=str) + "\n")
        last_hash = rows[-1].row_hash
        await db.execute(_delete(AuditLog).where(AuditLog.seq.in_([r.seq for r in rows])))
        await db.commit()
    await _audit.record("audit.archived", category="audit", source="scheduler",
                        details={"archived": len(rows), "first_seq": rows[0].seq, "last_seq": rows[-1].seq,
                                 "last_removed_hash": last_hash, "file": name, "retention_days": days})
    return {"archived": len(rows), "file": name}


@app.task(bind=True, max_retries=0)
def archive_audit(self):
    return asyncio.run(async_archive_audit())
