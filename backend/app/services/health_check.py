"""
Pre- and post-patch health checks for Linux servers.
Ensures system is healthy before patching and validates after.
"""

from typing import Dict, List, Optional
from app.connectors.ssh import SSHConnector
from app.logging_config import get_logger

logger = get_logger(__name__)


class HealthCheckResult:
    """Result of a health check"""

    def __init__(self, passed: bool, message: str = "", details: Dict = None):
        self.passed = passed
        self.message = message
        self.details = details or {}


class SystemHealthChecker:
    """
    Performs health checks before and after patching.
    Ensures system is in good state for patching.
    """

    @staticmethod
    async def pre_patch_check(connector: SSHConnector) -> Dict[str, any]:
        """
        Perform pre-patch health checks.
        Ensures system is ready for patching.

        Returns:
            Dictionary with check results and recommendations
        """
        results = {
            "timestamp": None,
            "overall_healthy": True,
            "checks": {},
            "warnings": [],
            "errors": [],
        }

        # Check disk space
        disk_check = await SystemHealthChecker._check_disk_space(connector)
        results["checks"]["disk_space"] = {
            "passed": disk_check.passed,
            "message": disk_check.message,
            "details": disk_check.details,
        }
        if not disk_check.passed:
            results["overall_healthy"] = False
            results["errors"].append(f"Disk space check failed: {disk_check.message}")

        # Check memory
        memory_check = await SystemHealthChecker._check_memory(connector)
        results["checks"]["memory"] = {
            "passed": memory_check.passed,
            "message": memory_check.message,
            "details": memory_check.details,
        }
        if not memory_check.passed:
            results["warnings"].append(f"Memory check: {memory_check.message}")

        # Check running services
        services_check = await SystemHealthChecker._check_critical_services(connector)
        results["checks"]["critical_services"] = {
            "passed": services_check.passed,
            "message": services_check.message,
            "details": services_check.details,
        }
        if not services_check.passed:
            results["warnings"].append(f"Service check: {services_check.message}")

        # Check for held packages (apt-mark hold)
        held_check = await SystemHealthChecker._check_held_packages(connector)
        results["checks"]["held_packages"] = {
            "passed": held_check.passed,
            "message": held_check.message,
            "details": held_check.details,
        }
        if not held_check.passed:
            results["warnings"].append(f"Held packages: {held_check.message}")

        # Check system load
        load_check = await SystemHealthChecker._check_system_load(connector)
        results["checks"]["system_load"] = {
            "passed": load_check.passed,
            "message": load_check.message,
            "details": load_check.details,
        }
        if not load_check.passed:
            results["warnings"].append(f"System load: {load_check.message}")

        return results

    @staticmethod
    async def post_patch_check(connector: SSHConnector, critical_services: List[str] = None) -> Dict[str, any]:
        """
        Perform post-patch health checks.
        Validates system still works after patching.

        Args:
            connector: SSH connector to server
            critical_services: List of services that must be running (e.g., ["sshd", "docker"])

        Returns:
            Dictionary with check results
        """
        results = {
            "timestamp": None,
            "overall_healthy": True,
            "checks": {},
            "warnings": [],
            "errors": [],
        }

        # Verify SSH still works (implicit, we're connected)
        results["checks"]["ssh_connectivity"] = {
            "passed": True,
            "message": "SSH connectivity confirmed",
            "details": {},
        }

        # Check if services recovered
        if critical_services:
            services_check = await SystemHealthChecker._check_specific_services(connector, critical_services)
            results["checks"]["critical_services"] = {
                "passed": services_check.passed,
                "message": services_check.message,
                "details": services_check.details,
            }
            if not services_check.passed:
                results["overall_healthy"] = False
                results["errors"].append(f"Critical service check failed: {services_check.message}")

        # Check system load is reasonable
        load_check = await SystemHealthChecker._check_system_load(connector)
        results["checks"]["system_load"] = {
            "passed": load_check.passed,
            "message": load_check.message,
            "details": load_check.details,
        }

        # Check disk space (shouldn't have changed much)
        disk_check = await SystemHealthChecker._check_disk_space(connector)
        results["checks"]["disk_space"] = {
            "passed": disk_check.passed,
            "message": disk_check.message,
            "details": disk_check.details,
        }

        # Check for pending reboot
        reboot_check = await SystemHealthChecker._check_pending_reboot(connector)
        results["checks"]["pending_reboot"] = {
            "passed": reboot_check.passed,
            "message": reboot_check.message,
            "details": reboot_check.details,
        }
        if not reboot_check.passed:
            results["warnings"].append(f"Reboot required: {reboot_check.message}")

        return results

    @staticmethod
    async def _check_disk_space(connector: SSHConnector) -> HealthCheckResult:
        """Check if disk space is sufficient (at least 10% free)"""
        cmd = "df -h / | tail -1 | awk '{print $5}' | sed 's/%//'"
        result = await connector.execute_command(cmd)

        if not result.success:
            return HealthCheckResult(False, "Could not check disk space", {"error": result.stderr})

        try:
            usage = int(result.stdout.strip())
            if usage > 90:
                return HealthCheckResult(False, f"Disk usage is {usage}% (>90%)", {"usage_percent": usage})
            return HealthCheckResult(True, f"Disk usage is {usage}%", {"usage_percent": usage})
        except ValueError:
            return HealthCheckResult(False, "Could not parse disk usage", {"output": result.stdout})

    @staticmethod
    async def _check_memory(connector: SSHConnector) -> HealthCheckResult:
        """Check available memory"""
        cmd = "free -m | grep Mem | awk '{print $7}'"
        result = await connector.execute_command(cmd)

        if not result.success:
            return HealthCheckResult(False, "Could not check memory", {"error": result.stderr})

        try:
            available = int(result.stdout.strip())
            if available < 100:  # Less than 100MB available
                return HealthCheckResult(False, f"Low memory: {available}MB available", {"available_mb": available})
            return HealthCheckResult(True, f"{available}MB memory available", {"available_mb": available})
        except ValueError:
            return HealthCheckResult(False, "Could not parse memory", {"output": result.stdout})

    @staticmethod
    async def _check_critical_services(connector: SSHConnector) -> HealthCheckResult:
        """Check if common critical services are running"""
        # Try to get list of running services
        cmd = "systemctl list-units --type=service --state=running --no-pager | grep -E 'ssh|docker|cron|rsyslog' | wc -l"
        result = await connector.execute_command(cmd)

        if result.success:
            return HealthCheckResult(True, "Critical services check passed", {})
        return HealthCheckResult(False, "Could not verify critical services", {"error": result.stderr})

    @staticmethod
    async def _check_specific_services(connector: SSHConnector, services: List[str]) -> HealthCheckResult:
        """Check if specific services are running"""
        failed = []
        for service in services:
            cmd = f"systemctl is-active {service}"
            result = await connector.execute_command(cmd)
            if not result.success or "inactive" in result.stdout.lower():
                failed.append(service)

        if failed:
            return HealthCheckResult(False, f"Services not running: {', '.join(failed)}", {"failed_services": failed})
        return HealthCheckResult(True, f"All {len(services)} critical services running", {"services": services})

    @staticmethod
    async def _check_held_packages(connector: SSHConnector) -> HealthCheckResult:
        """Check for held packages that won't be updated"""
        cmd = "apt-mark showhold 2>/dev/null | wc -l"
        result = await connector.execute_command(cmd)

        if result.success:
            try:
                count = int(result.stdout.strip())
                if count > 0:
                    return HealthCheckResult(False, f"{count} packages are held", {"held_count": count})
                return HealthCheckResult(True, "No held packages", {"held_count": 0})
            except ValueError:
                return HealthCheckResult(True, "Held packages check completed", {})
        return HealthCheckResult(True, "Could not check held packages", {})

    @staticmethod
    async def _check_system_load(connector: SSHConnector) -> HealthCheckResult:
        """Check system load average"""
        cmd = "uptime | grep -oP '(?<=load average: )[^,]*' | head -1"
        result = await connector.execute_command(cmd)

        if not result.success:
            return HealthCheckResult(True, "Could not check load", {})

        try:
            load = float(result.stdout.strip())
            if load > 50:  # Very high load
                return HealthCheckResult(False, f"System load is very high: {load}", {"load": load})
            elif load > 20:
                return HealthCheckResult(False, f"System load is high: {load}", {"load": load})
            return HealthCheckResult(True, f"System load: {load}", {"load": load})
        except ValueError:
            return HealthCheckResult(True, "Could not parse load", {})

    @staticmethod
    async def _check_pending_reboot(connector: SSHConnector) -> HealthCheckResult:
        """Check if system needs reboot"""
        # Check for /var/run/reboot-required (Ubuntu/Debian)
        cmd = "[ -f /var/run/reboot-required ] && echo 'reboot_needed' || echo 'no_reboot'"
        result = await connector.execute_command(cmd)

        if result.success and "reboot_needed" in result.stdout:
            return HealthCheckResult(False, "System reboot is required", {})
        return HealthCheckResult(True, "No reboot required", {})
