"""
Patch safety checking service for Linux systems.
Detects potentially dangerous patches before installation.
"""

from enum import Enum
from typing import List, Dict, Tuple
from app.connectors.base import PatchInfo
from app.logging_config import get_logger

logger = get_logger(__name__)


class PatchSafetyLevel(str, Enum):
    """Safety levels for patches"""
    SAFE = "safe"              # Safe to apply immediately
    CAUTION = "caution"        # Requires review; may need testing
    DANGER = "danger"          # Known issues; recommend staging first
    BLOCKED = "blocked"        # Cannot apply; known breaking change


class PatchRiskFactor(Enum):
    """Risk factors that increase patch danger level"""
    KERNEL = "kernel"          # Kernel updates require reboot
    GLIBC = "glibc"           # C library changes affect all programs
    SYSTEMD = "systemd"       # System management daemon
    OPENSSL = "openssl"       # TLS/security library
    DOCKER = "docker"         # Container runtime
    DATABASE = "database"     # DB engines (postgres, mysql, etc.)
    NETWORK = "network"       # Networking stack (net*, ip*, iptables)
    STORAGE = "storage"       # Storage drivers (dm, md, nvme)
    FILESYSTEM = "filesystem" # Filesystem changes (ext4, btrfs, xfs)
    SECURITY = "security"     # Security tools (selinux, apparmor)


class PatchSafetyChecker:
    """
    Analyzes patches for safety before installation.
    Uses heuristics based on package name and known issues.
    """

    # Packages that always require approval due to critical nature
    CRITICAL_PACKAGES = {
        "kernel", "linux-image", "linux-headers",
        "glibc", "libc6",
        "systemd", "systemd-libs",
        "openssl", "libssl",
        "openssh-server", "openssh-client",
        "sudo",
        "grub", "grub-pc", "grub-efi",
        "cryptsetup", "lvm2",
    }

    # Packages that might affect many services
    RISKY_PACKAGES = {
        "docker.io", "docker-ce", "podman",
        "libssl1.1", "libssl3",
        "gcc-libs", "gcc-base",
        "curl", "wget",
        "perl", "python3",
    }

    # Known breaking patches (CVE/Issue -> affected versions)
    KNOWN_BREAKING_PATCHES = {
        # Example format:
        # "kernel-5.15.10": PatchSafetyLevel.BLOCKED,
    }

    @staticmethod
    def check_package_name(package_name: str, version: str = "") -> Tuple[PatchSafetyLevel, List[PatchRiskFactor]]:
        """
        Check if a package name indicates potential safety issues.

        Args:
            package_name: Package name (e.g., "linux-image-5.15.10")
            version: Package version

        Returns:
            Tuple of (safety_level, risk_factors)
        """
        risks = []
        lower_name = package_name.lower()

        # Check for critical packages
        if any(crit in lower_name for crit in ["kernel", "linux-image", "linux-headers"]):
            risks.append(PatchRiskFactor.KERNEL)

        if "glibc" in lower_name or lower_name == "libc6":
            risks.append(PatchRiskFactor.GLIBC)

        if "systemd" in lower_name:
            risks.append(PatchRiskFactor.SYSTEMD)

        if "openssl" in lower_name or "libssl" in lower_name:
            risks.append(PatchRiskFactor.OPENSSL)

        if any(docker in lower_name for docker in ["docker", "podman"]):
            risks.append(PatchRiskFactor.DOCKER)

        if any(db in lower_name for db in ["postgresql", "postgres", "mysql", "mariadb", "mongodb"]):
            risks.append(PatchRiskFactor.DATABASE)

        if any(net in lower_name for net in ["netdev", "iproute", "iptables", "nftables", "networkmanager"]):
            risks.append(PatchRiskFactor.NETWORK)

        if any(stor in lower_name for stor in ["dm-", "device-mapper", "mdadm", "nvme", "iscsi"]):
            risks.append(PatchRiskFactor.STORAGE)

        if any(fs in lower_name for fs in ["ext4", "btrfs", "xfs", "zfs", "fuse"]):
            risks.append(PatchRiskFactor.FILESYSTEM)

        if any(sec in lower_name for sec in ["selinux", "apparmor", "audit"]):
            risks.append(PatchRiskFactor.SECURITY)

        # Check for security patches (usually lower risk but still important)
        if "security" in lower_name or "-sec" in lower_name:
            # Security patch - keep but note caution for critical packages
            if risks:
                safety_level = PatchSafetyLevel.CAUTION
            else:
                safety_level = PatchSafetyLevel.SAFE
        else:
            # Determine safety level based on risk factors
            if PatchRiskFactor.KERNEL in risks:
                safety_level = PatchSafetyLevel.DANGER  # Kernel always needs caution
            elif PatchRiskFactor.GLIBC in risks or PatchRiskFactor.SYSTEMD in risks:
                safety_level = PatchSafetyLevel.DANGER
            elif len(risks) >= 2:
                safety_level = PatchSafetyLevel.CAUTION
            elif risks:
                safety_level = PatchSafetyLevel.CAUTION
            else:
                safety_level = PatchSafetyLevel.SAFE

        return safety_level, risks

    @staticmethod
    def get_reboot_required_patches(patches: List[str]) -> List[str]:
        """
        Check which patches require a system reboot.

        Args:
            patches: List of package names

        Returns:
            List of patches that require reboot
        """
        reboot_patches = []
        for patch in patches:
            patch_lower = patch.lower()
            # Patches that definitely require reboot
            if any(kernel in patch_lower for kernel in ["kernel", "linux-image", "linux-headers", "grub"]):
                reboot_patches.append(patch)
            elif "glibc" in patch_lower or patch_lower == "libc6":
                reboot_patches.append(patch)
            elif "systemd" in patch_lower:
                reboot_patches.append(patch)
            elif any(pkg in patch_lower for pkg in ["openssl-server", "openssh-server"]):
                reboot_patches.append(patch)
        return reboot_patches

    @staticmethod
    def check_patch_combination(patches: List[str], critical_applications: List[str] = None) -> Dict[str, any]:
        """
        Check if a combination of patches might interact dangerously.

        Args:
            patches: List of package names/versions to patch
            critical_applications: List of detected critical apps (nginx, docker, kubernetes, etc)

        Returns:
            Dictionary with analysis results
        """
        results = {
            "total_patches": len(patches),
            "critical_patches": [],
            "risky_patches": [],
            "safe_patches": [],
            "warnings": [],
            "overall_safety": PatchSafetyLevel.SAFE,
            "reboot_required_patches": [],
            "requires_reboot": False,
        }

        critical_count = 0
        caution_count = 0

        # Check for reboot-required patches
        results["reboot_required_patches"] = PatchSafetyChecker.get_reboot_required_patches(patches)
        if results["reboot_required_patches"]:
            results["requires_reboot"] = True
            results["warnings"].append(
                f"Reboot required: {', '.join(results['reboot_required_patches'][:2])}"
                + (f" +{len(results['reboot_required_patches'])-2} more" if len(results['reboot_required_patches']) > 2 else "")
            )

        for patch in patches:
            safety_level, risks = PatchSafetyChecker.check_package_name(patch)

            if safety_level == PatchSafetyLevel.SAFE:
                results["safe_patches"].append(patch)
            elif safety_level == PatchSafetyLevel.CAUTION:
                results["risky_patches"].append(patch)
                caution_count += 1
            elif safety_level in [PatchSafetyLevel.DANGER, PatchSafetyLevel.BLOCKED]:
                results["critical_patches"].append(patch)
                critical_count += 1

        # Check for application-breaking patches
        if critical_applications:
            patch_lower = " ".join([p.lower() for p in patches])

            # nginx + http/openssl patches
            if "nginx" in critical_applications and any(pkg in patch_lower for pkg in ["openssl", "libssl", "http"]):
                results["warnings"].append(
                    "⚠️ nginx detected: Patches to OpenSSL/HTTP libraries may affect nginx. "
                    "Recommend staging deployment or maintenance window."
                )
                if caution_count < 1:
                    caution_count += 1

            # docker + container runtime patches
            if "docker" in critical_applications and any(pkg in patch_lower for pkg in ["docker", "libseccomp", "cgroup"]):
                results["warnings"].append(
                    "⚠️ Docker detected: Container runtime patches may cause downtime. "
                    "Ensure no critical containers depend on this server."
                )
                if caution_count < 1:
                    caution_count += 1

            # kubernetes + system patches
            if "kubernetes" in critical_applications and any(pkg in patch_lower for pkg in ["kernel", "systemd", "cgroup"]):
                results["warnings"].append(
                    "⚠️ Kubernetes detected: System patches may affect pod scheduling. "
                    "Cordon node before patching if possible."
                )
                if caution_count < 1:
                    caution_count += 1

            # gitlab + database/system patches
            if "gitlab" in critical_applications and any(pkg in patch_lower for pkg in ["postgresql", "redis", "systemd"]):
                results["warnings"].append(
                    "⚠️ GitLab detected: Database/system patches may require GitLab restart. "
                    "Schedule patching during maintenance window."
                )
                if caution_count < 1:
                    caution_count += 1

            # Database servers + any system patches
            db_apps = [app for app in critical_applications if app in ["postgres", "mysql", "mariadb", "mongodb"]]
            if db_apps and any(pkg in patch_lower for pkg in ["kernel", "systemd", "glibc"]):
                results["warnings"].append(
                    f"⚠️ Database {db_apps[0]} detected: System patches may cause service interruption. "
                    "Ensure backups are current and schedule during maintenance window."
                )
                if caution_count < 1:
                    caution_count += 1

        # Determine overall safety
        if critical_count > 0:
            results["overall_safety"] = PatchSafetyLevel.DANGER
            results["warnings"].append(f"{critical_count} critical/dangerous patches detected")
        elif caution_count > 3:
            results["overall_safety"] = PatchSafetyLevel.CAUTION
            results["warnings"].append(f"{caution_count} patches require caution")
        elif caution_count > 0:
            results["overall_safety"] = PatchSafetyLevel.CAUTION

        # Warn about kernel + other critical patches together
        if any("kernel" in p.lower() for p in results["critical_patches"]):
            if len(results["critical_patches"]) > 1:
                results["warnings"].append(
                    "Kernel patch combined with other critical patches. "
                    "Consider applying kernel separately."
                )

        # Warn about database patches
        db_patches = [p for p in patches if any(db in p.lower() for db in ["postgresql", "mysql", "mongodb"])]
        if db_patches:
            results["warnings"].append(
                f"Database patch detected ({db_patches[0]}). "
                "Ensure database is backed up before patching."
            )

        return results

    @staticmethod
    def get_patch_recommendation(patch_name: str) -> Dict[str, any]:
        """
        Get detailed recommendation for a single patch.
        """
        safety_level, risks = PatchSafetyChecker.check_package_name(patch_name)

        recommendation = {
            "patch": patch_name,
            "safety_level": safety_level,
            "risk_factors": [r.value for r in risks],
            "require_approval": safety_level in [PatchSafetyLevel.DANGER, PatchSafetyLevel.BLOCKED],
            "require_maintenance_window": safety_level in [PatchSafetyLevel.DANGER, PatchSafetyLevel.BLOCKED],
            "require_backup": PatchRiskFactor.DATABASE in risks,
            "requires_reboot": PatchRiskFactor.KERNEL in risks or PatchRiskFactor.GLIBC in risks,
            "recommendation": "",
        }

        # Generate text recommendation
        if safety_level == PatchSafetyLevel.BLOCKED:
            recommendation["recommendation"] = "This patch is blocked due to known breaking changes. Do not apply."
        elif safety_level == PatchSafetyLevel.DANGER:
            recommendation["recommendation"] = (
                "This patch requires approval and should be tested on staging first. "
                "Apply during maintenance window only."
            )
        elif safety_level == PatchSafetyLevel.CAUTION:
            recommendation["recommendation"] = (
                "This patch requires review. Consider testing on a staging server first."
            )
        else:
            recommendation["recommendation"] = "This patch is safe to apply."

        return recommendation
