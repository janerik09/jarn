import asyncio
from typing import List, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models import Server, PatchJob
from app.models.server import OSType, AuthMethod
from app.connectors import SSHConnector
from app.connectors.ssh import pinned_host_key
from app.logging_config import get_logger

logger = get_logger(__name__)


class PreflightChecker:
    """Validates server dependencies before patch execution"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_job_readiness(
        self, job_id: str
    ) -> Dict[str, Any]:
        """Check if all servers are ready for patching"""
        job = await self.db.get(PatchJob, job_id)
        if not job:
            return {"ready": False, "error": "Job not found"}

        results = {}
        tasks = []

        for server_id in job.server_ids:
            server = await self.db.get(Server, server_id)
            if not server:
                results[server_id] = {
                    "ready": False,
                    "checks": {"server_exists": "✗ failed: not found"}
                }
            else:
                tasks.append(self._check_server(server))

        # Run checks in parallel
        check_results = await asyncio.gather(*tasks)

        for server_id, result in zip(job.server_ids, check_results):
            results[server_id] = result

        # Overall readiness
        all_ready = all(r.get("ready") for r in results.values())

        return {
            "job_id": str(job.id),
            "ready": all_ready,
            "checked_at": datetime.utcnow().isoformat(),
            "results": results,
            "blockers": self._extract_blockers(results)
        }

    async def _check_server(self, server: Server) -> Dict[str, Any]:
        """Check individual server readiness"""
        checks = {}

        try:
            # Connectivity check
            connectivity_result = await self._check_connectivity(server)
            checks["connectivity"] = connectivity_result

            if "failed" in connectivity_result:
                return {
                    "ready": False,
                    "checks": checks
                }

            # Get appropriate connector
            connector = self._get_connector(server)

            # Connect to perform detailed checks
            if not await asyncio.wait_for(connector.connect(), timeout=30):
                checks["connection"] = "✗ failed: authentication failed"
                return {"ready": False, "checks": checks}

            # Run OS-specific checks
            checks.update(await self._check_linux(connector))

            await connector.disconnect()

        except asyncio.TimeoutError:
            checks["connectivity"] = "✗ failed: timeout"
            return {"ready": False, "checks": checks}
        except Exception as e:
            checks["error"] = f"✗ failed: {str(e)}"
            return {"ready": False, "checks": checks}

        # Determine readiness
        blockers = [v for k, v in checks.items() if "failed" in v.lower()]
        ready = len(blockers) == 0

        return {
            "ready": ready,
            "checks": checks,
            "blockers": blockers if blockers else None
        }

    async def _check_connectivity(self, server: Server) -> str:
        """Check if server is reachable"""
        try:
            connector = self._get_connector(server)
            result = await asyncio.wait_for(
                connector.connect(), timeout=10
            )
            await connector.disconnect()

            if result:
                return "✓ passed"
            else:
                return "✗ failed: connection refused"
        except asyncio.TimeoutError:
            return "✗ failed: timeout (server may be down)"
        except Exception as e:
            return f"✗ failed: {str(e)}"

    async def _check_linux(self, connector: SSHConnector) -> Dict[str, str]:
        """Check Linux-specific dependencies"""
        checks = {}

        # Check package manager
        pm_check = await connector.execute_command(
            "which apt-get || which yum || which dnf || which pacman"
        )
        if pm_check.success:
            if "apt-get" in pm_check.stdout:
                pm_type = "apt (Debian/Ubuntu)"
            elif "yum" in pm_check.stdout:
                pm_type = "yum (CentOS/RedHat)"
            elif "dnf" in pm_check.stdout:
                pm_type = "dnf (Fedora)"
            else:
                pm_type = "pacman (Arch)"
            checks["package_manager"] = f"✓ passed ({pm_type})"
        else:
            checks["package_manager"] = "✗ failed: no package manager found"

        # Check disk space
        df_check = await connector.execute_command(
            "df -BG / | tail -1 | awk '{print $4}'"
        )
        if df_check.success:
            try:
                available_gb = int(df_check.stdout.strip().replace("G", ""))
                if available_gb >= 1:
                    checks["disk_space"] = f"✓ passed ({available_gb}GB available)"
                else:
                    checks["disk_space"] = f"✗ failed: insufficient space ({available_gb}GB available)"
            except:
                checks["disk_space"] = "⚠ warning: could not determine disk space"
        else:
            checks["disk_space"] = "⚠ warning: could not check disk space"

        # Check if package manager is locked
        lock_check = await connector.execute_command(
            "lsof /var/lib/apt/lists/lock 2>/dev/null || echo 'free'"
        )
        if "free" in lock_check.stdout:
            checks["package_manager_lock"] = "✓ passed (no lock)"
        else:
            checks["package_manager_lock"] = "⚠ warning: package manager may be locked"

        # Check sudo access
        sudo_check = await connector.execute_command(
            "sudo -n true 2>&1 || echo 'requires_password'"
        )
        if "requires_password" not in sudo_check.stdout:
            checks["sudo_access"] = "✓ passed (no password required)"
        else:
            checks["sudo_access"] = "⚠ warning: sudo may require password"

        return checks

        return checks

    def _get_connector(self, server: Server):
        """Get appropriate connector for server"""
        return SSHConnector(
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            username=server.credentials.get("username"),
            private_key=server.credentials.get("private_key"),
            host_key=pinned_host_key(server.metadata_),
        )

    @staticmethod
    def _is_linux(os_type: OSType) -> bool:
        """Check if OS is Linux"""
        return os_type in [
            OSType.ubuntu, OSType.debian,
            OSType.centos, OSType.redhat, OSType.fedora
        ]

    @staticmethod
    def _extract_blockers(results: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract blocking issues from results"""
        blockers = []

        for server_id, result in results.items():
            # Early failures (connectivity, authentication) return without a blockers list, so the failed checks are used
            issues = result.get("blockers") or [v for v in (result.get("checks") or {}).values() if "failed" in str(v).lower()]
            if not result.get("ready") and issues:
                blockers.append({
                    "server_id": server_id,
                    "issues": issues
                })

        return blockers
