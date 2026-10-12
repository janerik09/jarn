"""
Server inventory: OS, services, containers, listening ports, and application health.
Collected when a server is added and on demand. Stored on the server record as metadata_["inventory"].
"""

from datetime import datetime
from typing import Any, Dict

from app.services.app_profiles import match_profiles
from app.services.risk_analysis import detect_applications, run_health_checks


def _parse_os_release(text: str) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            data[key.strip()] = val.strip().strip('"')
    return data


async def collect_inventory(connector) -> Dict[str, Any]:
    os_data = _parse_os_release(
        (await connector.execute_command("cat /etc/os-release", timeout=15)).stdout
    )
    kernel = (await connector.execute_command("uname -r", timeout=15)).stdout.strip()
    uptime_raw = (await connector.execute_command("cut -d' ' -f1 /proc/uptime", timeout=15)).stdout.strip()

    units, containers, paths = await detect_applications(connector)
    profiles = match_profiles(units, containers, paths)
    health = await run_health_checks(connector, profiles)

    ports_out = await connector.execute_command(
        "sudo ss -tlnp 2>/dev/null | awk 'NR>1{print $4}' | sort -u", timeout=30
    )
    ports = [p.strip() for p in (ports_out.stdout or "").splitlines() if p.strip()]

    applications = []
    for profile in profiles:
        checks = {key.split(": ", 1)[1]: health[key] for key in health if key.startswith(f"{profile.id}: ")}
        applications.append({
            "id": profile.id,
            "name": profile.name,
            "containerized": profile.containerized,
            "drain_before_patch": profile.drain_before_patch,
            "healthy": all(v["ok"] for v in checks.values()) if checks else None,
            "checks": [{"command": k, "ok": v["ok"], "detail": v["detail"]} for k, v in checks.items()],
        })

    return {
        "collected_at": datetime.utcnow().isoformat(),
        "os": {
            "id": os_data.get("ID"),
            "version": os_data.get("VERSION_ID"),
            "pretty_name": os_data.get("PRETTY_NAME"),
        },
        "kernel": kernel,
        "uptime_seconds": int(float(uptime_raw)) if uptime_raw.replace(".", "", 1).isdigit() else None,
        "kubernetes_node": any(p.id == "kubernetes_node" for p in profiles),
        "applications": applications,
        "services": units,
        "containers": [{"name": n, "image": i} for n, i in containers],
        "ports": ports,
    }
