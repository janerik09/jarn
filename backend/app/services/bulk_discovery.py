import asyncio
import io
import ipaddress
import socket
from typing import Any, Dict, List, Optional

import paramiko
from app.connectors.ssh import fetch_host_key, host_key_fingerprint

from app.logging_config import get_logger

logger = get_logger(__name__)

MAX_ADDRESSES = 1024
CONCURRENCY = 20
PORT_PROBE_TIMEOUT = 2
SSH_TIMEOUT = 8

SUPPORTED_DISTROS = {
    "ubuntu": "ubuntu",
    "debian": "debian",
    "centos": "centos",
    "rhel": "redhat",
    "fedora": "fedora",
    "rocky": "rocky",
    "almalinux": "almalinux",
}


def parse_targets(spec: str, limit: int = MAX_ADDRESSES) -> List[str]:
    """Expand CIDR subnets, last-octet ranges (10.0.0.5-20), full ranges (10.0.0.5-10.0.0.20) and single IPs."""
    addresses: List[str] = []
    parts = [p.strip() for p in spec.replace("\n", ",").split(",") if p.strip()]
    if not parts:
        raise ValueError("No targets given")

    for part in parts:
        if "/" in part:
            net = ipaddress.ip_network(part, strict=False)
            if net.version != 4:
                raise ValueError(f"Only IPv4 is supported: {part}")
            if net.num_addresses > limit + 2:
                raise ValueError(f"{part} has {net.num_addresses} addresses; the limit is {limit}")
            hosts = net.hosts() if net.prefixlen < 31 else net
            addresses.extend(str(a) for a in hosts)
        elif "-" in part:
            start_s, end_s = [x.strip() for x in part.split("-", 1)]
            start = ipaddress.IPv4Address(start_s)
            if "." in end_s:
                end = ipaddress.IPv4Address(end_s)
            else:
                prefix = ".".join(start_s.split(".")[:3])
                end = ipaddress.IPv4Address(f"{prefix}.{end_s}")
            if end < start:
                raise ValueError(f"Range end is before start: {part}")
            if int(end) - int(start) + 1 > limit:
                raise ValueError(f"{part} has more than {limit} addresses")
            addresses.extend(str(ipaddress.IPv4Address(i)) for i in range(int(start), int(end) + 1))
        else:
            addresses.append(str(ipaddress.IPv4Address(part)))

        if len(addresses) > limit:
            raise ValueError(f"Too many addresses ({len(addresses)}); the limit is {limit}")

    return list(dict.fromkeys(addresses))


async def _port_open(ip: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=PORT_PROBE_TIMEOUT)
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


def _load_pkey(private_key_text: str) -> paramiko.PKey:
    for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
        try:
            return key_cls.from_private_key(io.StringIO(private_key_text))
        except paramiko.SSHException:
            continue
    raise ValueError("Unsupported or invalid private key")


def _inspect_host_sync(ip: str, port: int, username: str, password: Optional[str],
                       private_key: Optional[str], host_key: str) -> Dict[str, Any]:
    """Log in with the approved host key and decide whether the host is a supported Linux distribution."""
    from app.connectors.ssh import _KEY_CLASSES
    import base64
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    keytype, b64 = host_key.split()[:2]
    host_name = ip if port == 22 else f"[{ip}]:{port}"
    client.get_host_keys().add(host_name, keytype, _KEY_CLASSES[keytype](data=base64.b64decode(b64)))
    kwargs: Dict[str, Any] = {
        "port": port,
        "username": username,
        "timeout": SSH_TIMEOUT,
        "banner_timeout": SSH_TIMEOUT,
        "auth_timeout": SSH_TIMEOUT,
        "allow_agent": False,
        "look_for_keys": False,
    }
    if private_key:
        kwargs["pkey"] = _load_pkey(private_key)
    else:
        kwargs["password"] = password

    try:
        client.connect(ip, **kwargs)
    except paramiko.AuthenticationException:
        return {"status": "auth_failed", "detail": "SSH login rejected with the given credentials"}
    except (OSError, paramiko.SSHException) as e:
        return {"status": "unreachable", "detail": f"SSH error: {e}"}

    try:
        _, stdout, _ = client.exec_command("uname -s; cat /etc/os-release 2>/dev/null", timeout=SSH_TIMEOUT)
        output = stdout.read().decode("utf-8", errors="replace")
    except (OSError, paramiko.SSHException) as e:
        return {"status": "unreachable", "detail": f"Command failed: {e}"}
    finally:
        client.close()

    lines = output.splitlines()
    kernel = lines[0].strip() if lines else ""
    if kernel != "Linux":
        return {"status": "not_linux", "detail": f"Not a Linux host (uname: {kernel or 'no response'})"}

    os_data: Dict[str, str] = {}
    for line in lines[1:]:
        if "=" in line:
            key, val = line.split("=", 1)
            os_data[key.strip()] = val.strip().strip('"')

    distro_id = os_data.get("ID", "").lower()
    os_type = SUPPORTED_DISTROS.get(distro_id)
    if not os_type:
        return {"status": "unsupported_linux",
                "detail": f"Linux distribution '{distro_id or 'unknown'}' is not supported"}

    return {
        "status": "linux",
        "os_type": os_type,
        "os_version": os_data.get("VERSION_ID") or None,
        "detail": os_data.get("PRETTY_NAME", ""),
    }


async def _reverse_dns(ip: str) -> Optional[str]:
    try:
        name, _, _ = await asyncio.wait_for(asyncio.to_thread(socket.gethostbyaddr, ip), timeout=3)
        return name
    except (OSError, asyncio.TimeoutError):
        return None


async def _discover_one(ip: str, port: int, username: str, password: Optional[str],
                        private_key: Optional[str], semaphore: asyncio.Semaphore,
                        approved: Dict[str, str]) -> Dict[str, Any]:
    """Two steps. First the host's key is read without sending anything. Credentials are only used when the key's
    fingerprint matches one an administrator approved for this address."""
    async with semaphore:
        result: Dict[str, Any] = {"ip": ip}
        if not await _port_open(ip, port):
            result.update(status="no_ssh", detail=f"Port {port} closed or filtered")
            return result
        try:
            key_line = await asyncio.to_thread(fetch_host_key, ip, port)
        except OSError as e:
            result.update(status="unreachable", detail=f"SSH error: {e}")
            return result
        fingerprint = host_key_fingerprint(key_line)
        result["host_key_fingerprint"] = fingerprint
        if approved.get(ip) != fingerprint:
            result.update(status="host_key_unapproved",
                          detail=f"Host key {fingerprint} is not approved yet. Compare it with the server, then approve it.")
            return result

        info = await asyncio.to_thread(_inspect_host_sync, ip, port, username, password, private_key, key_line)
        result.update(info)
        result["host_key"] = key_line
        if info["status"] == "linux":
            result["hostname"] = await _reverse_dns(ip) or ip
        return result


async def run_discovery(addresses: List[str], port: int, username: str,
                        password: Optional[str], private_key: Optional[str],
                        approved: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    approved = approved or {}
    tasks = [_discover_one(ip, port, username, password, private_key, semaphore, approved) for ip in addresses]
    results = await asyncio.gather(*tasks)
    return sorted(results, key=lambda r: tuple(int(x) for x in r["ip"].split(".")))
