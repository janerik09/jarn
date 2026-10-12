from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional


@dataclass
class PatchInfo:
    """Represents a patch available on a system"""
    name: str
    version: str
    patch_type: str  # security, bugfix, enhancement
    description: Optional[str] = None
    cve_ids: List[str] = None

    def __post_init__(self):
        if self.cve_ids is None:
            self.cve_ids = []


@dataclass
class CommandResult:
    """Result of executing a command on a remote system"""
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    @property
    def success(self) -> bool:
        return self.returncode == 0


class RemoteConnector(ABC):
    """Base class for remote system connectors (SSH)"""

    def __init__(self, hostname: str, **connection_params):
        self.hostname = hostname
        self.connection_params = connection_params

    @abstractmethod
    async def connect(self) -> bool:
        """Establish connection to remote system"""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection"""

    @abstractmethod
    async def execute_command(self, command: str, timeout: int = 30) -> CommandResult:
        """Execute a command on the remote system"""

    @abstractmethod
    async def scan_available_patches(self) -> List[PatchInfo]:
        """Scan for available patches on the system"""

    @abstractmethod
    async def install_patches(self, patches: List[str], dry_run: bool = False) -> CommandResult:
        """Install specific patches on the system"""

    @abstractmethod
    async def rollback_patches(self) -> CommandResult:
        """Rollback last patch installation"""

    @abstractmethod
    async def get_installed_patches(self) -> List[PatchInfo]:
        """Get list of currently installed patches"""

    @abstractmethod
    async def reboot(self, force: bool = False) -> CommandResult:
        """Reboot the system after patching"""

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Get system health information"""
