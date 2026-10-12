"""
Distro upgrade service for safe OS version upgrades.
Handles detection, analysis, and safe execution of distro upgrades.
"""

from typing import Dict, List, Any, Optional
from enum import Enum
from app.logging_config import get_logger

logger = get_logger(__name__)


class UpgradeStatus(str, Enum):
    """Status of distro upgrade readiness"""
    READY = "ready"                    # Can proceed with upgrade
    BLOCKED = "blocked"                # Cannot upgrade
    RISKY = "risky"                    # Can upgrade but has risks
    NOT_SUPPORTED = "not_supported"    # Upgrade path not supported


class DistroUpgradeChecker:
    """
    Analyzes distro upgrade readiness and plans safe upgrades.
    """

    # Supported upgrade paths
    SUPPORTED_UPGRADES = {
        "debian-10": ["debian-11", "debian-12"],
        "debian-11": ["debian-12"],
        "debian-12": ["debian-13"],
        "debian-13": [],  # Latest
        "ubuntu-20.04": ["ubuntu-22.04", "ubuntu-24.04"],
        "ubuntu-22.04": ["ubuntu-24.04"],
        "ubuntu-24.04": [],  # Latest
    }

    # Critical services that might be affected
    CRITICAL_SERVICES = {
        "nginx", "apache2", "docker", "kubernetes",
        "postgresql", "mysql", "mariadb", "mongodb", "redis",
        "gitlab", "jenkins", "zulip", "discourse",
        "haproxy", "envoy", "consul", "vault",
    }

    # Packages that commonly cause issues during upgrades
    PROBLEMATIC_PACKAGES = {
        "nvidia-driver-*": "GPU drivers may need reinstall after upgrade",
        "zfs-*": "ZFS module may need recompile",
        "virtualbox-*": "Virtualbox modules need recompile",
        "kernel-*": "Custom kernels need rebuild",
        "docker-ce": "Docker may need reconfiguration",
        "postgresql-*": "Major version upgrades require data migration",
    }

    CODENAMES = {
        ("debian", "11"): "bullseye",
        ("debian", "12"): "bookworm",
        ("debian", "13"): "trixie",
        ("ubuntu", "20.04"): "focal",
        ("ubuntu", "22.04"): "jammy",
        ("ubuntu", "24.04"): "noble",
    }

    @staticmethod
    def codename_for(os_type: str, version: str) -> Optional[str]:
        return DistroUpgradeChecker.CODENAMES.get((os_type.lower(), version))

    @staticmethod
    def get_current_distro(os_type: str, os_version: str) -> str:
        """Get distro identifier"""
        return f"{os_type}-{os_version}".lower()

    @staticmethod
    def get_available_upgrades(current_distro: str) -> List[str]:
        """Get available upgrade paths from current distro"""
        return DistroUpgradeChecker.SUPPORTED_UPGRADES.get(current_distro, [])

    @staticmethod
    async def analyze_upgrade_readiness(
        connector,
        os_type: str,
        os_version: str,
        target_version: str,
        running_services: List[str]
    ) -> Dict[str, Any]:
        """
        Analyze if system is ready for distro upgrade.

        Returns:
            Dict with upgrade readiness analysis
        """
        current_distro = DistroUpgradeChecker.get_current_distro(os_type, os_version)
        target_distro = f"{os_type}-{target_version}".lower()

        analysis = {
            "current_distro": current_distro,
            "target_distro": target_distro,
            "upgrade_status": UpgradeStatus.BLOCKED,
            "warnings": [],
            "blockers": [],
            "recommendations": [],
            "estimated_duration_minutes": 30,
            "disk_space_required_gb": 5,
        }

        # Check if upgrade path is supported
        available = DistroUpgradeChecker.get_available_upgrades(current_distro)
        if target_distro not in available:
            analysis["blockers"].append(
                f"Upgrade from {current_distro} to {target_distro} not supported. "
                f"Available upgrades: {', '.join(available) if available else 'None - already latest'}"
            )
            analysis["upgrade_status"] = UpgradeStatus.NOT_SUPPORTED
            return analysis

        # Check critical services
        critical_running = [s for s in running_services if s in DistroUpgradeChecker.CRITICAL_SERVICES]
        if critical_running:
            analysis["warnings"].append(
                f"Critical services running: {', '.join(critical_running)}. "
                f"These may be temporarily unavailable during upgrade."
            )
            analysis["upgrade_status"] = UpgradeStatus.RISKY

        # Check for problematic packages
        logger.info("checking_problematic_packages", hostname=connector.hostname)
        try:
            result = await connector.execute_command(
                "dpkg -l | grep -E '^ii' | awk '{print $2}'",
                timeout=10
            )
            if result.success:
                installed_packages = result.stdout.lower().split('\n')
                problematic = []
                for pattern, issue in DistroUpgradeChecker.PROBLEMATIC_PACKAGES.items():
                    pattern_base = pattern.replace('*', '').rstrip('-')
                    for pkg in installed_packages:
                        if pkg.startswith(pattern_base):
                            problematic.append(f"{pkg}: {issue}")
                            break

                if problematic:
                    analysis["warnings"].extend(problematic)
                    analysis["upgrade_status"] = UpgradeStatus.RISKY
        except Exception as e:
            logger.warning("failed_to_check_packages", error=str(e))

        # Check disk space (need ~2-3x distro size)
        logger.info("checking_disk_space", hostname=connector.hostname)
        try:
            result = await connector.execute_command(
                "df / | tail -1 | awk '{print $4}'",
                timeout=10
            )
            if result.success:
                available_kb = int(result.stdout.strip())
                available_gb = available_kb / (1024 * 1024)
                if available_gb < analysis["disk_space_required_gb"]:
                    analysis["blockers"].append(
                        f"Insufficient disk space. Required: {analysis['disk_space_required_gb']}GB, "
                        f"Available: {available_gb:.1f}GB"
                    )
                    analysis["upgrade_status"] = UpgradeStatus.BLOCKED
                else:
                    analysis["recommendations"].append(
                        f"Disk space OK ({available_gb:.1f}GB available)"
                    )
        except Exception as e:
            logger.warning("failed_to_check_disk_space", error=str(e))

        # Check for custom kernel
        try:
            result = await connector.execute_command(
                "uname -r | grep -q custom && echo yes || echo no",
                timeout=10
            )
            if result.success and "yes" in result.stdout.lower():
                analysis["warnings"].append(
                    "Custom kernel detected. May need rebuild after upgrade."
                )
        except Exception as e:
            logger.debug("failed_to_check_kernel", error=str(e))

        # Determine final status
        if not analysis["blockers"] and not analysis["warnings"]:
            analysis["upgrade_status"] = UpgradeStatus.READY
            analysis["recommendations"].append("System is ready for upgrade")
        elif analysis["blockers"]:
            analysis["upgrade_status"] = UpgradeStatus.BLOCKED
        elif not analysis["blockers"] and analysis["warnings"]:
            analysis["upgrade_status"] = UpgradeStatus.RISKY
            analysis["recommendations"].append("Review warnings before proceeding")

        return analysis

    @staticmethod
    def get_pre_upgrade_steps() -> List[str]:
        """Get recommended pre-upgrade steps"""
        return [
            "1. Backup critical data and configurations",
            "2. Document current system state (dpkg -l > packages.txt)",
            "3. Stop non-essential services",
            "4. Clear package cache to free space",
            "5. Run full system update (apt update && apt upgrade)",
            "6. Reboot to apply any pending kernel updates",
            "7. Verify all services are running correctly",
        ]

    @staticmethod
    def get_post_upgrade_steps() -> List[str]:
        """Get recommended post-upgrade steps"""
        return [
            "1. Verify all services are running",
            "2. Check system logs for errors",
            "3. Verify database integrity",
            "4. Run application health checks",
            "5. Monitor resource usage and performance",
            "6. Document any manual interventions required",
            "7. Keep rollback plan accessible for 7 days",
        ]

    @staticmethod
    def get_rollback_plan(os_type: str, os_version: str) -> Dict[str, Any]:
        """Get rollback procedure"""
        return {
            "method": "snapshot-restore",
            "description": "Restore from pre-upgrade system snapshot",
            "difficulty": "medium",
            "estimated_time_minutes": 45,
            "steps": [
                "1. Boot into recovery/grub menu",
                "2. Restore filesystem from snapshot (if available)",
                "3. Restore from backup if no snapshot available",
                "4. Verify system boots correctly",
                "5. Restore critical data from backup",
                "6. Verify all services and applications",
            ],
            "notes": "Snapshot-based rollback is recommended. Ensure backups are current before upgrade.",
        }
