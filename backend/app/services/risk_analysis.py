"""
Pre-patch impact analysis: which running services use files owned by packages in a job, and which
application profiles those services belong to.

Read-only. The script lists each running service's loaded program files and libraries, then maps
them to owning packages with dpkg. A service whose files belong to a job package will be running
old code until it is restarted, and the patch may change how it behaves.
"""

import base64
from typing import Any, Dict, List

from app.services.app_profiles import BASELINE_UNITS, PROFILES, AppProfile, match_profiles

SCRIPT = r"""#!/bin/sh
TMP=$(mktemp)
systemctl list-units --type=service --state=running --no-legend --plain | awk '{print $1}' > $TMP.units
while read u; do
  pid=$(systemctl show -p MainPID --value "$u" 2>/dev/null)
  if [ -z "$pid" ] || [ "$pid" = "0" ]; then continue; fi
  echo "UNIT $u"
  { readlink /proc/$pid/exe; awk '{print $6}' /proc/$pid/maps 2>/dev/null; } | grep '^/' | sed 's/ (deleted)$//' | sort -u | sed 's/^/FILE /'
done < $TMP.units > $TMP.out
grep '^FILE ' $TMP.out | cut -c6- | sort -u | xargs -r dpkg -S 2>/dev/null | sed 's/^/OWN /'
cat $TMP.out
rm -f $TMP $TMP.units $TMP.out
"""


def _parse(output: str) -> Dict[str, Any]:
    units: Dict[str, set] = {}
    file_owner: Dict[str, str] = {}
    unit_files: Dict[str, set] = {}
    current = None
    for line in output.splitlines():
        if line.startswith("UNIT "):
            current = line[5:].strip()
            units.setdefault(current, set())
            unit_files.setdefault(current, set())
        elif line.startswith("FILE ") and current:
            unit_files[current].add(line[5:].strip())
        elif line.startswith("OWN "):
            body = line[4:]
            if ": " in body:
                owner, path = body.split(": ", 1)
                file_owner[path.strip()] = owner.split(":")[0].strip()
    for unit, files in unit_files.items():
        for f in files:
            if f in file_owner:
                units[unit].add(file_owner[f])
    return {"units": {u: sorted(p) for u, p in units.items()}}


async def detect_applications(connector, parsed: Dict[str, Any] = None):
    """Running services, running containers as (name, image), and profile paths that exist."""
    if parsed is None:
        encoded = base64.b64encode(SCRIPT.encode()).decode()
        script_run = await connector.execute_command(
            f"echo {encoded} | base64 -d > /tmp/pm_impact.sh && sudo sh /tmp/pm_impact.sh; rm -f /tmp/pm_impact.sh",
            timeout=300,
        )
        parsed = _parse(script_run.stdout or "")
    running_units = sorted(parsed["units"])

    listing = await connector.execute_command("sudo docker ps --format '{{.Names}}\\t{{.Image}}' 2>/dev/null", timeout=30)
    containers = []
    for line in (listing.stdout or "").splitlines():
        if "\t" in line:
            name, image = line.split("\t", 1)
            containers.append((name.strip(), image.strip()))

    candidate_paths = sorted({p for profile in PROFILES for p in profile.detect_paths})
    path_check = " ".join(candidate_paths) or "/dev/null"
    existing = await connector.execute_command(f"for p in {path_check}; do [ -e \"$p\" ] && echo \"$p\"; done", timeout=30)
    existing_paths = [p.strip() for p in (existing.stdout or "").splitlines() if p.strip()]
    return running_units, containers, existing_paths


async def detect_profiles(connector) -> List[AppProfile]:
    running_units, containers, existing_paths = await detect_applications(connector)
    return match_profiles(running_units, containers, existing_paths)


async def run_health_checks(connector, profiles: List[AppProfile]) -> Dict[str, Dict[str, Any]]:
    """Run every profile health command. Keys are 'profile: command'; ok means exit code 0."""
    results: Dict[str, Dict[str, Any]] = {}
    for profile in profiles:
        for command in profile.health_commands:
            outcome = await connector.execute_command(command, timeout=60)
            results[f"{profile.id}: {command}"] = {
                "ok": outcome.returncode == 0,
                "detail": (outcome.stdout or outcome.stderr or "")[-200:],
            }
    return results


async def analyze_server(connector, job_packages: List[str]) -> Dict[str, Any]:
    encoded = base64.b64encode(SCRIPT.encode()).decode()
    script_run = await connector.execute_command(
        f"echo {encoded} | base64 -d > /tmp/pm_impact.sh && sudo sh /tmp/pm_impact.sh; rm -f /tmp/pm_impact.sh",
        timeout=300,
    )
    parsed = _parse(script_run.stdout or "")
    running_units = sorted(parsed["units"])

    running_units, containers, existing_paths = await detect_applications(connector, parsed=parsed)

    job_set = set(job_packages)
    affected_units = {u: sorted(set(pkgs) & job_set) for u, pkgs in parsed["units"].items() if set(pkgs) & job_set}

    profiles = []
    for profile in match_profiles(running_units, containers, existing_paths):
        own_units = [u for u in running_units if u in profile.detect_units or u in profile.companion_units]
        hit = {u: affected_units[u] for u in own_units if u in affected_units}
        if not hit and profile.containerized and "docker.service" in affected_units:
            hit = {"docker.service": affected_units["docker.service"]}
        held = sorted(job_set & set(profile.hold_packages))
        profiles.append({
            "id": profile.id,
            "name": profile.name,
            "units": own_units,
            "affected": bool(hit),
            "affected_by": sorted({p for pkgs in hit.values() for p in pkgs}),
            "held_packages": held,
            "drain_before_patch": profile.drain_before_patch,
            "guidance": profile.guidance,
        })

    profile_units = {u for p in profiles for u in p["units"]}
    unknown_affected = {
        u: pkgs for u, pkgs in affected_units.items()
        if u not in profile_units and u not in BASELINE_UNITS
    }
    held_all = sorted({pkg for p in profiles for pkg in p["held_packages"]})
    requires_approval = any(p["affected"] for p in profiles) or bool(unknown_affected) or bool(held_all)

    warnings = []
    for p in profiles:
        if p["affected"]:
            warnings.append(f"{p['name']} is affected by: {', '.join(p['affected_by'])}. It will need a restart.")
        if p["held_packages"]:
            warnings.append(f"{p['name']} packages in this job: {', '.join(p['held_packages'])}.")
        if (p["affected"] or p["held_packages"]) and p.get("guidance"):
            warnings.append(f"{p['name']}: {p['guidance']}")
    for unit, pkgs in sorted(unknown_affected.items()):
        warnings.append(f"Unrecognised service {unit} is affected by: {', '.join(pkgs)}. It will need a restart.")

    return {
        "profiles": profiles,
        "unknown_affected_services": unknown_affected,
        "held_packages": held_all,
        "drain_required": any(p["drain_before_patch"] for p in profiles),
        "requires_approval": requires_approval,
        "warnings": warnings,
        "running_services": len(running_units),
    }


async def analyze_job(servers: List[Dict[str, Any]], patch_ids: List[str]) -> List[Dict[str, Any]]:
    import asyncio
    from app.connectors.ssh import SSHConnector
    from app.tasks import resolve_patch_packages

    semaphore = asyncio.Semaphore(6)

    async def one(server: Dict[str, Any]) -> Dict[str, Any]:
        base = {"server_id": server["id"], "hostname": server["hostname"]}
        async with semaphore:
            creds = server["creds"]
            connector = SSHConnector(
                hostname=server["ip"] or server["hostname"],
                username=creds.get("username"),
                private_key=creds.get("private_key"),
                password=creds.get("password"),
                port=server["port"],
                host_key=server.get("host_key"),
            )
            try:
                if not await connector.connect():
                    return {**base, "ok": False, "error": "SSH connection failed", "requires_approval": False,
                            "drain_required": False, "warnings": []}
                packages, unresolved = await resolve_patch_packages(connector, patch_ids)
                analysis = await analyze_server(connector, packages)
                plan = await connector.simulate_install(packages)
                if plan["remove"]:
                    analysis["warnings"] = analysis.get("warnings", []) + [
                        f"Would remove {len(plan['remove'])} package(s): " + ", ".join(plan["remove"][:15])
                    ]
                previous = await connector.save_package_versions(packages)
                rollback_unavailable = await connector.check_rollback_available(previous)
                if rollback_unavailable:
                    analysis["warnings"] = analysis.get("warnings", []) + [
                        "No longer offered by any repository, so a failed patch cannot be rolled back by package "
                        "(a VM restore would be needed): " + ", ".join(rollback_unavailable[:15])
                    ]
                return {**base, "ok": True, "packages": packages, "unresolved": unresolved,
                        "rollback_unavailable": rollback_unavailable, "removals": plan["remove"], **analysis}
            except Exception as e:
                return {**base, "ok": False, "error": str(e), "requires_approval": False,
                        "drain_required": False, "warnings": []}
            finally:
                try:
                    await connector.disconnect()
                except Exception:
                    pass

    results = await asyncio.gather(*(one(s) for s in servers))
    return sorted(results, key=lambda r: r["hostname"])
