import asyncio
import base64
import hashlib
import re
import shlex
import time
from typing import List, Dict, Any, Optional
import paramiko
from paramiko.ssh_exception import SSHException
from app.connectors.base import RemoteConnector, PatchInfo, CommandResult
from app.logging_config import get_logger

logger = get_logger(__name__)


HOST_KEY_FIELD = "ssh_host_key"
_KEY_CLASSES = {
    "ssh-ed25519": paramiko.Ed25519Key,
    "ssh-rsa": paramiko.RSAKey,
    "ecdsa-sha2-nistp256": paramiko.ECDSAKey,
    "ecdsa-sha2-nistp384": paramiko.ECDSAKey,
    "ecdsa-sha2-nistp521": paramiko.ECDSAKey,
}


def pinned_host_key(metadata: Optional[Dict[str, Any]]) -> Optional[str]:
    """The approved host key line ("ssh-ed25519 AAAA...") stored on a server, if an administrator has approved one."""
    return (metadata or {}).get(HOST_KEY_FIELD) or None


def host_key_fingerprint(host_key_line: str) -> str:
    """SHA256 fingerprint in the form ssh-keygen prints, so an administrator can compare it with the server."""
    _, b64 = host_key_line.split()[:2]
    digest = hashlib.sha256(base64.b64decode(b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def fetch_host_key(hostname: str, port: int = 22, timeout: int = 15) -> str:
    """The host key a server presents, read before any login. No credentials are sent."""
    import socket
    sock = socket.create_connection((hostname, port), timeout=timeout)
    transport = None
    try:
        transport = paramiko.Transport(sock)
        transport.start_client(timeout=timeout)
        key = transport.get_remote_server_key()
        return f"{key.get_name()} {key.get_base64()}"
    finally:
        if transport is not None:
            transport.close()
        sock.close()


def _content_digest(packages: Dict[str, str]) -> str:
    """Short stable digest of package names and target versions."""
    text = "\n".join(f"{name}={version}" for name, version in sorted(packages.items()))
    return hashlib.sha256(text.encode()).hexdigest()[:8]


class SSHConnector(RemoteConnector):
    """SSH connector for Linux systems (Debian, Ubuntu, CentOS, RedHat, Fedora)"""

    def __init__(self, hostname: str, username: str, **connection_params):
        super().__init__(hostname, **connection_params)
        self.username = username
        self.client = None
        self.connected = False
        self.scan_info: Dict[str, Any] = {}
        self.connect_error: Optional[str] = None

    async def connect(self) -> bool:
        ok = await asyncio.to_thread(self._connect_sync)
        from app.services import audit
        await audit.record(
            "ssh.connect", category="ssh", outcome="success" if ok else "failure",
            resource_type="server", reason=None if ok else (self.connect_error or "SSH connection failed"),
            details={"host": self.hostname, "user": self.username,
                     "method": "key" if self.connection_params.get("private_key") else "password",
                     "port": self.connection_params.get("port")},
        )
        return ok

    def _connect_sync(self) -> bool:
        """Connect via SSH using key or password authentication"""
        try:
            # Only the approved key is accepted. An unapproved or changed key means the connection may be intercepted,
            # so credentials are not sent.
            pinned = self.connection_params.get("host_key")
            if not pinned:
                self.connect_error = "No SSH host key is approved for this server. Approve it under Admin Settings, Server host keys."
                logger.warning("ssh_no_approved_host_key", hostname=self.hostname)
                return False
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
            keytype, b64 = pinned.split()[:2]
            key_class = _KEY_CLASSES.get(keytype)
            if key_class is None:
                self.connect_error = f"Unsupported approved host key type: {keytype}"
                return False
            port = int(self.connection_params.get("port") or 22)
            host_name = self.hostname if port == 22 else f"[{self.hostname}]:{port}"
            self.client.get_host_keys().add(host_name, keytype, key_class(data=base64.b64decode(b64)))

            kwargs = {
                "username": self.username,
                "port": port,  # without this, servers on a non-standard SSH port were dialled on 22
                "timeout": self.connection_params.get("timeout", 30),
            }

            # SSH key authentication
            if self.connection_params.get("private_key"):
                try:
                    from io import StringIO
                    key_file = StringIO(self.connection_params["private_key"])
                    private_key = paramiko.RSAKey.from_private_key(key_file)
                    kwargs["pkey"] = private_key
                except Exception as e:
                    logger.error("ssh_key_parse_failed", hostname=self.hostname, error=str(e))
                    return False

            # Password authentication
            elif self.connection_params.get("password"):
                kwargs["password"] = self.connection_params["password"]

            self.client.connect(self.hostname, **kwargs)
            self.connected = True
            logger.info("ssh_connected", hostname=self.hostname)
            return True

        except paramiko.BadHostKeyException:
            self.connect_error = ("The server's SSH host key has changed since it was approved. Check with the server's "
                                  "owner; approve the new key under Admin Settings only if the change is expected.")
            logger.error("ssh_host_key_changed", hostname=self.hostname)
            return False
        except SSHException as e:
            logger.error("ssh_connection_failed", hostname=self.hostname, error=str(e))
            return False
        except Exception as e:
            logger.error("ssh_unexpected_error", hostname=self.hostname, error=str(e))
            return False

    async def disconnect(self) -> None:
        if self.client:
            self.client.close()
            self.connected = False
            logger.info("ssh_disconnected", hostname=self.hostname)

    def _make_command_noninteractive(self, cmd: str, os_type: str = "linux") -> str:
        """
        Wrap command with non-interactive safeguards
        Handles common interactive prompts across different tools
        """
        # Add timeout to prevent indefinite hangs
        # Use 'yes' to automatically answer 'y' to prompts
        # Redirect stderr for cleaner output
        safe_cmd = f"timeout 180 bash -c 'yes | {cmd}' 2>&1"
        return safe_cmd

    async def test_connection(self) -> bool:
        """Test SSH connection without keeping it open"""
        try:
            connected = await self.connect()
            if connected:
                await self.disconnect()
            return connected
        except Exception as e:
            logger.error("connection_test_failed", hostname=self.hostname, error=str(e))
            return False

    def run_stream(self, command: str, *, upload_path: Optional[str] = None, download=None,
                   timeout: int = 3600) -> tuple:
        """Run a command that moves a large file in one direction. Returns (exit_status, stderr).

        upload_path: a local file sent to the command's stdin.
        download: a function called with each chunk of the command's stdout (used for archives).
        A sudo password is sent first on stdin, as in _execute_command_sync, so an upload cannot be combined with sudo.
        """
        import threading
        if not self.connected or self.client is None:
            return 1, "Not connected"
        password = self.connection_params.get("password")
        use_password = bool(password) and "sudo" in command
        if upload_path and use_password:
            raise ValueError("A command that uploads data cannot also use a sudo password on stdin")
        if use_password:
            command = command.replace("sudo ", "sudo -S ")
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        channel = stdout.channel
        channel.settimeout(timeout)
        # stderr is read on its own thread so a full stderr buffer cannot stall the archive stream
        stderr_chunks: List[bytes] = []
        reader = threading.Thread(target=lambda: stderr_chunks.append(stderr.read()), daemon=True)
        reader.start()
        try:
            if use_password:
                stdin.write(password + "\n")
            if upload_path:
                with open(upload_path, "rb") as source:
                    for chunk in iter(lambda: source.read(65536), b""):
                        stdin.write(chunk)
            # Close stdin so a remote command that reads it cannot wait forever
            stdin.flush()
            channel.shutdown_write()
            while True:
                chunk = stdout.read(65536)
                if not chunk:
                    break
                if download is not None:
                    download(chunk)
            returncode = channel.recv_exit_status()
        finally:
            reader.join(timeout=30)
        return returncode, b"".join(stderr_chunks).decode("utf-8", errors="replace")

    async def execute_command(self, command: str, timeout: int = 30) -> CommandResult:
        import time
        from app.services import audit
        started = time.monotonic()
        result = await asyncio.to_thread(self._execute_command_sync, command, timeout)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Encoded scripts are unreadable, so the record keeps their hash and size instead
        if "base64 -d" in command:
            import hashlib
            shown = f"[encoded script, {len(command)} characters, sha256 {hashlib.sha256(command.encode()).hexdigest()[:16]}]"
        else:
            shown = command[:1000]
        await audit.record(
            "ssh.exec", category="ssh", outcome="success" if result.returncode == 0 else "failure",
            resource_type="server", reason=(result.stderr or "").strip()[:300] or None,
            details={"host": self.hostname, "user": self.username, "command": shown,
                     "exit_code": result.returncode, "duration_ms": elapsed_ms},
        )
        return result

    def _execute_command_sync(self, command: str, timeout: int = 30) -> CommandResult:
        if not self.connected:
            return CommandResult(
                returncode=1,
                stdout="",
                stderr="Not connected",
                duration_seconds=0
            )

        try:
            start = time.time()

            # If command has sudo and we have a password, add -S flag to read from stdin
            password = self.connection_params.get("password")
            if "sudo" in command and password:
                command = command.replace("sudo ", "sudo -S ")
                stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
                stdin.write(password + "\n")
                stdin.flush()
            else:
                stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)

            # Set socket timeout to enforce hard time limit on the channel
            stdout.channel.settimeout(timeout)
            stderr.channel.settimeout(timeout)

            # Read output (paramiko will respect the channel timeout)
            stdout_text = ""
            stderr_text = ""

            try:
                stdout_text = stdout.read().decode('utf-8')
            except Exception as e:
                stdout_text = f"[Stdout read error: {str(e)}]"

            try:
                stderr_text = stderr.read().decode('utf-8')
            except Exception as e:
                stderr_text = f"[Stderr read error: {str(e)}]"

            try:
                returncode = stdout.channel.recv_exit_status()
            except Exception as e:
                returncode = 124  # timeout exit code
                stderr_text += f"\n[Exit status error: {str(e)}]"

            duration = time.time() - start

            # If command took longer than timeout, mark as timed out
            if duration > timeout:
                returncode = 124
                stderr_text += f"\n[Command exceeded {timeout}s timeout]"

            return CommandResult(
                returncode=returncode,
                stdout=stdout_text,
                stderr=stderr_text,
                duration_seconds=duration
            )
        except Exception as e:
            logger.error("ssh_command_failed", hostname=self.hostname, command=command, error=str(e))
            return CommandResult(
                returncode=1,
                stdout="",
                stderr=str(e),
                duration_seconds=0
            )

    async def scan_available_patches(self) -> List[PatchInfo]:
        """Scan available patches based on OS type"""
        if not self.connected:
            return []

        # Detect OS type first
        os_detect = await self.execute_command(
            "cat /etc/os-release | grep -E 'ID=|VERSION_ID='"
        )

        if os_detect.returncode != 0:
            return []

        os_info = os_detect.stdout.lower()
        patches = []

        # Debian/Ubuntu - create dependency-aware bundles
        if "debian" in os_info or "ubuntu" in os_info:
            refresh = await self.execute_command("sudo apt-get update -qq", timeout=180)
            result = await self.execute_command(
                "apt list --upgradable 2>/dev/null | tail -n +2"
            )
            if result.success:
                upgradable = [line for line in result.stdout.splitlines() if line.strip()]
                self.scan_info = {
                    "index_refreshed": refresh.success,
                    "upgradable_packages": len(upgradable),
                    "security_packages": sum(1 for line in upgradable if "-security" in line),
                }
                patches = await self._create_apt_patch_bundles(result.stdout)

        # CentOS/RedHat/Fedora
        elif "centos" in os_info or "rhel" in os_info or "fedora" in os_info:
            result = await self.execute_command(
                "yum check-update 2>/dev/null"
            )
            if result.success:
                patches = self._parse_yum_patches(result.stdout)

        logger.info("patch_scan_complete", hostname=self.hostname, patch_count=len(patches))
        return patches

    def _parse_apt_patches(self, output: str) -> List[PatchInfo]:
        """Parse apt list output into PatchInfo objects, including dependencies"""
        patches = []
        packages_to_install = {}

        for line in output.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('/')
            if len(parts) >= 2:
                package_name = parts[0].strip()
                version = parts[1].strip().split()[0] if len(parts[1].split()) > 0 else ""
                packages_to_install[package_name] = version

        # For each package, get its dependencies using apt-cache
        for package_name, version in packages_to_install.items():
            patch_type = "enhancement"

            # Check if it's a security update by looking at security repos
            # This is a simplified check - in production would parse apt-cache more thoroughly
            description = f"Available update to {version}"

            patches.append(PatchInfo(
                name=package_name,
                version=version,
                patch_type=patch_type,
                description=description
            ))

        return patches

    def _parse_yum_patches(self, output: str) -> List[PatchInfo]:
        """Parse yum check-update output into PatchInfo objects"""
        patches = []
        for line in output.strip().split('\n'):
            if not line.strip() or line.startswith(' '):
                continue
            parts = line.split()
            if len(parts) >= 3:
                package_name = parts[0]
                new_version = parts[1]
                patch_type = "enhancement"
                patches.append(PatchInfo(
                    name=package_name,
                    version=new_version,
                    patch_type=patch_type
                ))
        return patches

    async def _create_apt_patch_bundles(self, output: str) -> List[PatchInfo]:
        """Create dependency-aware patch bundles for Debian/Ubuntu"""
        patches = []

        # Parse available packages
        available_packages = {}
        for line in output.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('/')
            if len(parts) >= 2:
                package_name = parts[0].strip()
                # apt lists "name/suite version arch [upgradable from: old]"; the version is the second token after the slash
                tokens = parts[1].split()
                version = tokens[1] if len(tokens) > 1 else (tokens[0] if tokens else "")
                available_packages[package_name] = version

        if not available_packages:
            return []

        # Get complete dependency closure for ALL packages at once (much faster)
        pkg_list = " ".join(list(available_packages.keys()))
        result = await self.execute_command(
            f"apt-get install --simulate {pkg_list} 2>/dev/null | grep '^Inst' | awk '{{print $2}}' | sort -u"
        )

        if not result.success or not result.stdout.strip():
            logger.warning("apt_simulate_failed", hostname=self.hostname)
            # Fallback: return all as one large bundle
            package_list = ", ".join(sorted(list(available_packages.keys()))[:5])
            if len(available_packages) > 5:
                package_list += f", +{len(available_packages) - 5} more"
            patches.append(PatchInfo(
                name=f"System Updates {_content_digest(available_packages)}",
                version=f"{len(available_packages)} packages",
                patch_type="bundle",
                description=f"Packages: {package_list}"
            ))
            return patches

        # Parse full dependency closure
        all_to_install = set(pkg.strip() for pkg in result.stdout.split('\n') if pkg.strip())
        upgradable_in_closure = [p for p in all_to_install if p in available_packages]

        if not upgradable_in_closure:
            upgradable_in_closure = list(available_packages.keys())

        # Create bundles by grouping related packages (packages with same prefix)
        bundles_dict = {}
        processed = set()

        for pkg_name in sorted(upgradable_in_closure):
            if pkg_name in processed:
                continue

            # Group packages with same base name (e.g., "lib*", "python*", etc)
            prefix = pkg_name.split('-')[0]
            bundle_key = f"bundle_{prefix}"

            if bundle_key not in bundles_dict:
                bundles_dict[bundle_key] = []

            bundles_dict[bundle_key].append(pkg_name)
            processed.add(pkg_name)

        # Create patch bundle entries
        for bundle_key, bundle_pkgs in sorted(bundles_dict.items()):
            # Store full package list in description for backend to use during install
            full_package_list = ", ".join(sorted(bundle_pkgs))
            # Also create a preview for display
            preview_list = ", ".join(sorted(bundle_pkgs)[:3])
            if len(bundle_pkgs) > 3:
                preview_list += f", +{len(bundle_pkgs) - 3} more"

            # The name identifies the content (packages and target versions), not the position in this scan.
            # Position-based names were reused on every scan, so a bundle deployed earlier hid newer upgrades.
            digest = _content_digest({p: available_packages.get(p, "") for p in bundle_pkgs})
            patches.append(PatchInfo(
                name=f"Bundle {bundle_key[len('bundle_'):]} {digest}",
                version=f"{len(bundle_pkgs)} packages",
                patch_type="bundle",
                description=f"Packages: {full_package_list}"
            ))

        logger.info("patch_bundles_created", hostname=self.hostname, bundle_count=len(patches), total_packages=len(available_packages))
        return patches

    async def get_patch_dependencies(self, package_name: str) -> List[str]:
        """Get all dependencies for a package using apt simulate"""
        if not self.connected:
            return []

        try:
            # Use apt simulate to get full dependency closure
            result = await self.execute_command(
                f"apt-get install --simulate {package_name} 2>/dev/null | grep '^Inst' | awk '{{print $2}}' | sort -u"
            )

            if result.success and result.stdout.strip():
                return [pkg.strip() for pkg in result.stdout.split('\n') if pkg.strip()]

            return []
        except Exception as e:
            logger.error("get_patch_dependencies_failed", package=package_name, error=str(e))
            return []

    async def install_patches(self, patches: List[str], dry_run: bool = False) -> CommandResult:
        """Install patches using package manager with robust prompt handling"""
        if not self.connected:
            return CommandResult(1, "", "Not connected", 0)

        # Detect package manager
        pm_result = await self.execute_command("which apt-get || which yum || which dnf")

        if "apt-get" in pm_result.stdout:
            # Debian/Ubuntu - simple and reliable approach
            if dry_run:
                cmd = "sudo apt-get install --simulate " + " ".join(patches)
            else:
                # Simplified approach - just install without update
                # Update can hang on network issues, skip it since packages should be cached
                # Use environment variables and flags to prevent all interactive prompts
                cmd = (
                    "DEBIAN_FRONTEND=noninteractive "
                    "sudo -E apt-get install -y "
                    "-o DPkg::options::=--force-confdef "
                    "-o DPkg::options::=--force-confold "
                    "-qq --no-install-recommends " + " ".join(patches)
                )
        elif "yum" in pm_result.stdout or "dnf" in pm_result.stdout:
            # CentOS/RedHat/Fedora with automatic yes to all prompts
            if dry_run:
                cmd = "sudo yum install -y --assumeno " + " ".join(patches)
            else:
                pkg_manager = "dnf" if "dnf" in pm_result.stdout else "yum"
                # -y answers yes to all prompts
                # -q for quiet mode
                # --skip-broken to continue on dependency issues
                cmd = f"sudo {pkg_manager} install -y -q --skip-broken " + " ".join(patches)
        else:
            return CommandResult(1, "", "Unknown package manager", 0)

        # Use shorter timeout since we're not doing update
        return await self.execute_command(cmd, timeout=3000)

    async def verify_packages_upgraded(self, packages: List[str]) -> bool:
        """True when none of the packages are still upgradable and dpkg reports a consistent state."""
        audit = await self.execute_command("dpkg --audit 2>&1; echo audit-finished", timeout=60)
        if audit.stdout.replace("audit-finished", "").strip():
            return False
        pending = await self.execute_command("apt list --upgradable 2>/dev/null | tail -n +2 | cut -d/ -f1", timeout=60)
        return not (set(pending.stdout.split()) & set(packages))

    async def rollback_patches(self, package_names: List[str], previous_versions: Dict[str, str]) -> CommandResult:
        """
        Rollback patches by downgrading packages to previous versions.

        Args:
            package_names: List of packages to rollback
            previous_versions: Dict of package -> version mapping

        Returns:
            CommandResult of rollback operation
        """
        if not self.connected:
            return CommandResult(1, "", "Not connected", 0)

        try:
            # Detect package manager
            pm_result = await self.execute_command("which apt-get || which yum || which dnf")

            if "apt-get" in pm_result.stdout:
                # Debian/Ubuntu rollback via version pinning
                rollback_cmd = "sudo apt-get install -y --allow-downgrades "
                for pkg in package_names:
                    if pkg in previous_versions:
                        rollback_cmd += f"{shlex.quote(pkg)}={shlex.quote(previous_versions[pkg])} "
                    else:
                        rollback_cmd += f"{shlex.quote(pkg)} "
                return await self.execute_command(rollback_cmd, timeout=300)

            elif "yum" in pm_result.stdout or "dnf" in pm_result.stdout:
                # RedHat/CentOS/Fedora rollback
                pm_cmd = "dnf" if "dnf" in pm_result.stdout else "yum"
                rollback_cmd = f"sudo {pm_cmd} downgrade -y "
                for pkg in package_names:
                    if pkg in previous_versions:
                        rollback_cmd += f"{pkg}-{previous_versions[pkg]} "
                    else:
                        rollback_cmd += f"{pkg} "
                return await self.execute_command(rollback_cmd, timeout=300)

            else:
                return CommandResult(1, "", "Unknown package manager for rollback", 0)

        except Exception as e:
            logger.error("rollback_failed", error=str(e))
            return CommandResult(1, "", f"Rollback error: {str(e)}", 0)

    async def get_installed_patches(self) -> List[PatchInfo]:
        """Get list of installed packages"""
        patches = []

        # Try dpkg
        result = await self.execute_command("sudo dpkg -l | grep '^ii' | awk '{print $2, $3}'")
        if result.success:
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 2:
                        patches.append(PatchInfo(
                            name=parts[0],
                            version=parts[1],
                            patch_type="installed"
                        ))

        return patches

    async def reboot(self, force: bool = False) -> CommandResult:
        """Reboot the system"""
        cmd = "sudo shutdown -r now" if force else "sudo shutdown -r +1 'System will reboot in 1 minute'"
        return await self.execute_command(cmd)

    async def health_check(self) -> Dict[str, Any]:
        """Get system health information"""
        if not self.connected:
            return {"status": "disconnected"}

        try:
            uptime = await self.execute_command("uptime")
            disk = await self.execute_command("df -h | grep -E '^/dev/'")
            memory = await self.execute_command("free -h")

            return {
                "status": "healthy" if all([uptime.success, disk.success, memory.success]) else "degraded",
                "uptime": uptime.stdout if uptime.success else "",
                "disk": disk.stdout if disk.success else "",
                "memory": memory.stdout if memory.success else "",
            }
        except Exception as e:
            logger.error("health_check_failed", hostname=self.hostname, error=str(e))
            return {"status": "unhealthy", "error": str(e)}

    async def get_patch_safety_analysis(self, patches: List[str]) -> Dict[str, Any]:
        """
        Analyze patch safety before installation.

        Args:
            patches: List of package names to analyze

        Returns:
            Dictionary with safety analysis
        """
        from app.services.patch_safety import PatchSafetyChecker
        return PatchSafetyChecker.check_patch_combination(patches)

    async def pre_patch_health_check(self) -> Dict[str, Any]:
        """
        Perform pre-patch health validation.
        Ensures system is ready for patching.
        """
        from app.services.health_check import SystemHealthChecker
        return await SystemHealthChecker.pre_patch_check(self)

    async def post_patch_health_check(self, critical_services: List[str] = None) -> Dict[str, Any]:
        """
        Perform post-patch health validation.
        Ensures system is still healthy after patching.

        Args:
            critical_services: List of services to verify (e.g., ['sshd', 'docker'])
        """
        from app.services.health_check import SystemHealthChecker
        return await SystemHealthChecker.post_patch_check(self, critical_services)

    async def get_package_version(self, package: str) -> Optional[str]:
        """
        Get currently installed version of a package.

        Args:
            package: Package name

        Returns:
            Version string or None if not installed
        """
        # Try apt first (Debian/Ubuntu)
        if not re.fullmatch(r"[a-z0-9][a-z0-9+.\-]+", package):
            return None
        result = await self.execute_command(f"dpkg-query -W -f='${{Version}}\\n' {package} 2>/dev/null | head -1")
        if result.success and result.stdout.strip():
            return result.stdout.strip()

        # Try rpm (RedHat/CentOS/Fedora)
        result = await self.execute_command(f"rpm -q {package} --queryformat '%{{VERSION}}'")
        if result.success and result.stdout.strip():
            return result.stdout.strip()

        return None

    async def save_package_versions(self, packages: List[str]) -> Dict[str, str]:
        """
        Save current versions of packages for rollback.

        Args:
            packages: List of package names

        Returns:
            Dict of package -> version mapping
        """
        names = [p for p in packages if re.fullmatch(r"[a-z0-9][a-z0-9+.\-]+", p)]
        if not names:
            return {}
        result = await self.execute_command(
            "dpkg-query -W -f='${Package} ${Version}\\n' " + " ".join(names) + " 2>/dev/null", timeout=120
        )
        versions = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in names:
                versions[parts[0]] = parts[1]
        return versions

    async def preflight_system_health(self) -> Dict[str, Any]:
        """Package manager and disk state that would make an install unsafe or fail halfway."""
        locks = await self.execute_command(
            "pgrep -x 'apt|apt-get|dpkg|unattended-upgr' >/dev/null && echo busy || echo free", timeout=15
        )
        audit = await self.execute_command("dpkg --audit 2>&1 | head -5", timeout=60)
        disks = await self.execute_command(
            "df -P -B1 / /var /boot 2>/dev/null | awk 'NR>1{print $6, $4}'", timeout=15
        )
        free: Dict[str, int] = {}
        for line in disks.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                free[parts[0]] = int(parts[1])
        root_free = free.get("/", 0)
        return {
            "apt_busy": locks.stdout.strip() == "busy",
            "dpkg_problems": audit.stdout.strip(),
            "root_free_bytes": root_free,
            "var_free_bytes": free.get("/var", root_free),
            "boot_free_bytes": free.get("/boot", root_free),
        }

    async def config_file_list(self) -> List[str]:
        result = await self.execute_command(
            "find /etc -xdev \\( -name '*.dpkg-dist' -o -name '*.dpkg-new' \\) 2>/dev/null | sort | head -200", timeout=60
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    async def still_upgradable(self, packages: List[str]) -> List[str]:
        result = await self.execute_command(
            "apt list --upgradable 2>/dev/null | tail -n +2 | cut -d/ -f1", timeout=60
        )
        return sorted(set(result.stdout.split()) & set(packages))

    async def simulate_install(self, packages: List[str]) -> Dict[str, List[str]]:
        """Packages apt would install or remove for this set, without changing anything."""
        names = [p for p in packages if re.fullmatch(r"[a-z0-9][a-z0-9+.\-]+", p)]
        if not names:
            return {"install": [], "remove": []}
        result = await self.execute_command(
            "apt-get install --simulate " + " ".join(names) + " 2>/dev/null | grep -E '^(Inst|Remv) '", timeout=600
        )
        install, remove = set(), set()
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                (install if parts[0] == "Inst" else remove).add(parts[1])
        return {"install": sorted(install), "remove": sorted(remove)}

    async def check_rollback_available(self, previous_versions: Dict[str, str]) -> List[str]:
        """Packages whose previously installed version is no longer offered by any configured repository."""
        names = [p for p in previous_versions if re.fullmatch(r"[a-z0-9][a-z0-9+.\-]+", p)]
        if not names:
            return []
        result = await self.execute_command("apt-cache madison " + " ".join(names) + " 2>/dev/null", timeout=120)
        offered: Dict[str, set] = {}
        for line in result.stdout.splitlines():
            parts = [x.strip() for x in line.split("|")]
            if len(parts) >= 2:
                offered.setdefault(parts[0], set()).add(parts[1])
        return sorted(p for p in names if previous_versions[p] not in offered.get(p, set()))

    async def running_services(self) -> List[str]:
        result = await self.execute_command(
            "systemctl list-units --type=service --state=running --no-legend --plain | awk '{print $1}'", timeout=30
        )
        return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())

    async def failed_units(self) -> List[str]:
        result = await self.execute_command(
            "systemctl --failed --no-legend --plain | awk '{print $1}'", timeout=30
        )
        return sorted(line.strip() for line in result.stdout.splitlines() if line.strip())

    async def running_kernel(self) -> str:
        result = await self.execute_command("uname -r", timeout=15)
        return result.stdout.strip()

    async def newest_installed_kernel(self) -> str:
        result = await self.execute_command(
            "ls /boot/vmlinuz-* 2>/dev/null | sed 's#/boot/vmlinuz-##' | sort -V | tail -1", timeout=15
        )
        return result.stdout.strip()

    async def collect_health(self) -> Dict[str, Any]:
        """CPU (sampled over 1s), memory, root disk, load and uptime for the health panel."""
        result = await self.execute_command(
            "head -1 /proc/stat; sleep 1; head -1 /proc/stat; cat /proc/loadavg; nproc; "
            "free -b | awk 'NR==2{print $2, $3}'; df -P / | awk 'NR==2{print $5}'; cut -d' ' -f1 /proc/uptime",
            timeout=30,
        )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if len(lines) < 7:
            return {"error": "unexpected health output"}
        try:
            def cpu_counters(line: str):
                fields = [int(x) for x in line.split()[1:9]]
                return sum(fields), fields[3] + fields[4]

            total1, idle1 = cpu_counters(lines[0])
            total2, idle2 = cpu_counters(lines[1])
            delta_total = total2 - total1
            cpu_pct = round(100 * (1 - (idle2 - idle1) / delta_total), 1) if delta_total > 0 else None

            load1 = float(lines[2].split()[0])
            cores = int(lines[3])
            mem_total, mem_used = (int(x) for x in lines[4].split())
            return {
                "cpu_pct": cpu_pct,
                "memory_pct": round(100 * mem_used / mem_total, 1) if mem_total else None,
                "disk_pct": int(lines[5].strip().rstrip("%")),
                "load_1m": load1,
                "cpu_cores": cores,
                "uptime_seconds": int(float(lines[6])),
            }
        except (ValueError, IndexError) as e:
            return {"error": f"could not parse health output: {e}"}

    async def detect_os_version(self) -> Dict[str, Any]:
        """
        Detect the OS version and type running on the server.

        Returns:
            Dict with detected OS info
        """
        os_info = {
            "os_type": "linux",
            "os_version": None,
            "distro_name": None,
            "detection_method": None,
            "full_version": None,
        }

        try:
            # Try /etc/os-release first (modern approach, works on most distros)
            result = await self.execute_command("cat /etc/os-release 2>/dev/null", timeout=5)
            if result.success and result.stdout:
                os_data = {}
                for line in result.stdout.split('\n'):
                    if '=' in line:
                        key, val = line.split('=', 1)
                        os_data[key.lower()] = val.strip('"')

                if 'version_id' in os_data:
                    os_info["os_version"] = os_data['version_id']
                    os_info["detection_method"] = "os-release"

                if 'name' in os_data:
                    os_info["distro_name"] = os_data['name']

                if 'pretty_name' in os_data:
                    os_info["full_version"] = os_data['pretty_name']

                logger.info("os_version_detected_via_osrelease",
                           hostname=self.hostname,
                           version=os_info["os_version"],
                           distro=os_info["distro_name"])
                return os_info

        except Exception as e:
            logger.debug(f"Failed to detect via os-release: {str(e)}")

        try:
            # Try lsb_release command (Debian/Ubuntu)
            result = await self.execute_command("lsb_release -rs 2>/dev/null", timeout=5)
            if result.success and result.stdout.strip():
                os_info["os_version"] = result.stdout.strip()
                os_info["detection_method"] = "lsb_release"

                # Get distro name too
                result_name = await self.execute_command("lsb_release -is 2>/dev/null", timeout=5)
                if result_name.success:
                    os_info["distro_name"] = result_name.stdout.strip()

                logger.info("os_version_detected_via_lsb",
                           hostname=self.hostname,
                           version=os_info["os_version"])
                return os_info

        except Exception as e:
            logger.debug(f"Failed to detect via lsb_release: {str(e)}")

        try:
            # Try /etc/debian_version (Debian/Ubuntu)
            result = await self.execute_command("cat /etc/debian_version 2>/dev/null", timeout=5)
            if result.success and result.stdout.strip():
                version = result.stdout.strip()
                # Clean up version string (e.g., "13.2" from "13.2\n")
                os_info["os_version"] = version.split()[0]
                os_info["distro_name"] = "Debian"
                os_info["detection_method"] = "debian_version"

                logger.info("os_version_detected_via_debian_version",
                           hostname=self.hostname,
                           version=os_info["os_version"])
                return os_info

        except Exception as e:
            logger.debug(f"Failed to detect via debian_version: {str(e)}")

        try:
            # Try /etc/redhat-release (RedHat/CentOS/Fedora)
            result = await self.execute_command("cat /etc/redhat-release 2>/dev/null", timeout=5)
            if result.success and result.stdout.strip():
                # Extract version from lines like "CentOS Linux release 7.9.2009"
                import re
                match = re.search(r'release\s+([\d.]+)', result.stdout)
                if match:
                    os_info["os_version"] = match.group(1)
                    os_info["distro_name"] = result.stdout.split(' ')[0]
                    os_info["detection_method"] = "redhat_release"

                    logger.info("os_version_detected_via_redhat",
                               hostname=self.hostname,
                               version=os_info["os_version"])
                    return os_info

        except Exception as e:
            logger.debug(f"Failed to detect via redhat-release: {str(e)}")

        # If we got here, we couldn't detect the version
        logger.warning("os_version_detection_failed", hostname=self.hostname)
        return os_info

    async def detect_critical_applications(self) -> Dict[str, Any]:
        """
        Detect critical applications running on the server (nginx, kubernetes, gitlab, etc).

        Returns:
            Dict with detected applications and their status
        """
        detected = {
            "applications": [],
            "critical_services": [],
            "docker_running": False,
            "kubernetes_running": False,
        }

        # Check for common critical applications
        app_checks = {
            "nginx": "systemctl is-active nginx > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "apache2": "systemctl is-active apache2 > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "docker": "systemctl is-active docker > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "kubernetes": "which kubectl > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "gitlab": "systemctl is-active gitlab-runsvdir > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "postgres": "systemctl is-active postgresql > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "mysql": "systemctl is-active mysql > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "mariadb": "systemctl is-active mariadb > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "redis": "systemctl is-active redis-server > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
            "mongodb": "systemctl is-active mongod > /dev/null 2>&1 && echo 'running' || echo 'stopped'",
        }

        for app_name, check_cmd in app_checks.items():
            try:
                result = await self.execute_command(check_cmd, timeout=5)
                if result.success and "running" in result.stdout.lower():
                    detected["applications"].append(app_name)
                    if app_name in ["nginx", "apache2", "docker", "kubernetes", "gitlab", "postgres", "mysql", "mariadb"]:
                        detected["critical_services"].append(app_name)
                    if app_name == "docker":
                        detected["docker_running"] = True
                    if app_name == "kubernetes":
                        detected["kubernetes_running"] = True
            except Exception as e:
                logger.debug(f"Failed to check {app_name}: {str(e)}")
                continue

        return detected

    async def check_reboot_required(self) -> Dict[str, Any]:
        """
        Check if a reboot is already pending on the system.

        Returns:
            Dict with reboot status and reason
        """
        reboot_info = {
            "reboot_required": False,
            "reboot_reason": None,
            "reboot_pending_file": None,
        }

        # Check /var/run/reboot-required (Ubuntu/Debian)
        result = await self.execute_command("test -f /var/run/reboot-required && echo 'yes' || echo 'no'", timeout=5)
        if result.success and "yes" in result.stdout:
            reboot_info["reboot_required"] = True
            reboot_info["reboot_pending_file"] = "/var/run/reboot-required"

            # Try to get reason from /var/run/reboot-required.pkgs
            reason_result = await self.execute_command("cat /var/run/reboot-required.pkgs 2>/dev/null | head -5", timeout=5)
            if reason_result.success and reason_result.stdout.strip():
                reboot_info["reboot_reason"] = reason_result.stdout.strip()

        # Check for other reboot indicators
        if not reboot_info["reboot_required"]:
            # Check if kernel was updated (common in Debian/Ubuntu)
            kernel_result = await self.execute_command(
                "if [ -f /boot/vmlinuz-$(uname -r) ]; then echo 'current'; else echo 'needs_reboot'; fi",
                timeout=5
            )
            if kernel_result.success and "needs_reboot" in kernel_result.stdout:
                reboot_info["reboot_required"] = True
                reboot_info["reboot_reason"] = "Kernel version mismatch detected"

        return reboot_info

    async def reboot_server(self, timeout: int = 600, graceful: bool = True) -> Dict[str, Any]:
        """
        Reboot the server safely.

        Args:
            timeout: How long to wait for reboot (seconds)
            graceful: If True, use 'shutdown -r' for graceful reboot; if False, use 'reboot' immediately

        Returns:
            Dict with reboot status
        """
        try:
            # Check if already rebooting
            check_reboot = await self.execute_command("ps aux | grep -i reboot | grep -v grep", timeout=5)
            if check_reboot.success and check_reboot.stdout.strip():
                return {
                    "success": False,
                    "message": "Reboot already in progress",
                    "error": check_reboot.stdout.strip()
                }

            # Schedule graceful reboot if requested
            if graceful:
                reboot_cmd = "sudo shutdown -r +1 'System reboot initiated by JARN'"
            else:
                reboot_cmd = "sudo reboot"

            result = await self.execute_command(reboot_cmd, timeout=10)

            if result.success or "scheduled" in result.stdout.lower():
                logger.info("reboot_scheduled", hostname=self.hostname, graceful=graceful)
                return {
                    "success": True,
                    "message": "Reboot scheduled successfully",
                    "graceful": graceful,
                    "timeout": timeout if graceful else 0
                }
            else:
                logger.error("reboot_failed", hostname=self.hostname, error=result.stderr)
                return {
                    "success": False,
                    "message": "Failed to schedule reboot",
                    "error": result.stderr
                }

        except Exception as e:
            logger.error("reboot_error", hostname=self.hostname, error=str(e))
            return {
                "success": False,
                "message": "Error scheduling reboot",
                "error": str(e)
            }

    async def cancel_pending_reboot(self) -> Dict[str, Any]:
        """
        Cancel a pending reboot if one is scheduled.

        Returns:
            Dict with cancellation status
        """
        try:
            result = await self.execute_command("sudo shutdown -c", timeout=10)

            if result.success:
                logger.info("reboot_cancelled", hostname=self.hostname)
                return {
                    "success": True,
                    "message": "Pending reboot cancelled successfully"
                }
            else:
                return {
                    "success": False,
                    "message": "No pending reboot to cancel",
                    "error": result.stderr
                }

        except Exception as e:
            logger.error("cancel_reboot_error", hostname=self.hostname, error=str(e))
            return {
                "success": False,
                "message": "Error cancelling reboot",
                "error": str(e)
            }
