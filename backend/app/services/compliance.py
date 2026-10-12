"""
Compliance assessment: host checks run over SSH and patch-job history, mapped to framework requirements.

Control mappings are a starting point. Validate the requirement references against the
framework text with your auditor before relying on the report for an attestation.
"""

import csv
import io
import re
import textwrap
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.bulk_discovery import SUPPORTED_DISTROS

# End of standard security support. Verify against the vendor lifecycle pages before relying on them.
EOL_DATES: Dict[Tuple[str, str], date] = {
    ("debian", "9"): date(2022, 6, 30),
    ("debian", "10"): date(2024, 6, 30),
    ("debian", "11"): date(2026, 8, 31),
    ("debian", "12"): date(2028, 6, 30),
    ("debian", "13"): date(2030, 6, 30),
    ("ubuntu", "20.04"): date(2025, 5, 31),
    ("ubuntu", "22.04"): date(2027, 6, 1),
    ("ubuntu", "24.04"): date(2029, 6, 1),
    ("centos", "7"): date(2024, 6, 30),
    ("redhat", "8"): date(2029, 5, 31),
    ("redhat", "9"): date(2032, 5, 31),
    ("rocky", "8"): date(2029, 5, 31),
    ("rocky", "9"): date(2032, 5, 31),
    ("almalinux", "8"): date(2029, 3, 1),
    ("almalinux", "9"): date(2032, 5, 31),
}

COMMANDS: Dict[str, str] = {
    "os_release": "cat /etc/os-release 2>/dev/null",
    "sshd": "sudo sshd -T 2>/dev/null",
    "pass_max_days": "grep -E '^PASS_MAX_DAYS' /etc/login.defs 2>/dev/null",
    "firewall": "systemctl is-active ufw firewalld nftables 2>/dev/null",
    "auto_updates": "systemctl is-enabled unattended-upgrades apt-daily-upgrade.timer dnf-automatic.timer 2>/dev/null",
    # The security update setting itself, not just the timers that refresh package lists
    "auto_updates_config": (
        "if command -v apt-get >/dev/null 2>&1; then apt-config dump APT::Periodic::Unattended-Upgrade 2>/dev/null;"
        " elif [ -f /etc/dnf/automatic.conf ]; then grep -E '^[[:space:]]*apply_updates' /etc/dnf/automatic.conf 2>/dev/null; fi"
    ),
    # Refresh the package index first, so the count reflects what is pending now
    "security_updates": (
        "if command -v apt-get >/dev/null 2>&1; then sudo apt-get update -qq >/dev/null 2>&1 && echo refresh=ok || echo refresh=failed;"
        " apt-get -s upgrade 2>/dev/null | grep -c '^Inst.*-security';"
        " elif command -v dnf >/dev/null 2>&1; then sudo dnf -q makecache >/dev/null 2>&1 && echo refresh=ok || echo refresh=failed;"
        " sudo dnf -q updateinfo list --available --security 2>/dev/null | grep -c 'Sec\\.';"
        " else sudo yum -q makecache fast >/dev/null 2>&1 && echo refresh=ok || echo refresh=failed;"
        " sudo yum -q updateinfo list security available 2>/dev/null | grep -c 'Sec\\.'; fi"
    ),
    "reboot_pending": "test -f /var/run/reboot-required && echo yes || echo no",
    "audit": "systemctl is-active auditd rsyslog 2>/dev/null",
    "time_sync": "timedatectl show -p NTPSynchronized --value 2>/dev/null",
}

PATCH_RECENCY_DAYS = 30


def _lines(text: Optional[str]) -> List[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _parse_os_release(text: Optional[str]) -> Dict[str, str]:
    data: Dict[str, str] = {}
    for line in _lines(text):
        if "=" in line:
            key, val = line.split("=", 1)
            data[key.strip()] = val.strip().strip('"')
    return data


def _sshd_value(text: Optional[str], key: str) -> Optional[str]:
    for line in _lines(text):
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == key:
            return parts[1].lower()
    return None


def check_os_supported(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    osr = _parse_os_release(facts.get("os_release"))
    distro = SUPPORTED_DISTROS.get(osr.get("ID", "").lower())
    vid = osr.get("VERSION_ID", "")
    if not distro or not vid:
        return None, "operating system not identified"
    version = ".".join(vid.split(".")[:2]) if distro == "ubuntu" else vid.split(".")[0]
    eol = EOL_DATES.get((distro, version))
    if not eol:
        return None, f"{distro} {vid}: end-of-support date not in lifecycle table"
    if date.today() <= eol:
        return True, f"{distro} {vid} supported until {eol.isoformat()}"
    return False, f"{distro} {vid} reached end of support on {eol.isoformat()}"


def check_ssh_root_disabled(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    value = _sshd_value(facts.get("sshd"), "permitrootlogin")
    if value is None:
        return None, "sshd configuration not readable"
    return value in ("no", "prohibit-password", "without-password", "forced-commands-only"), f"PermitRootLogin {value}"


def check_ssh_password_disabled(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    value = _sshd_value(facts.get("sshd"), "passwordauthentication")
    if value is None:
        return None, "sshd configuration not readable"
    return value == "no", f"PasswordAuthentication {value}"


def check_password_max_age(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    for line in _lines(facts.get("pass_max_days")):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "PASS_MAX_DAYS" and parts[1].isdigit():
            days = int(parts[1])
            return days <= 90, f"PASS_MAX_DAYS {days}"
    if not facts.get("pass_max_days"):
        return None, "login.defs not readable"
    return False, "PASS_MAX_DAYS not set (passwords do not expire)"


def check_firewall_active(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    states = _lines(facts.get("firewall"))
    if not states:
        return None, "firewall state not reported"
    active = "active" in states
    return active, "a firewall service is active" if active else "no active firewall (ufw, firewalld, nftables)"


def check_auto_security_updates(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    config = " ".join(_lines(facts.get("auto_updates_config")))
    if 'Unattended-Upgrade "1"' in config:
        return True, "unattended security upgrades enabled"
    if re.search(r"apply_updates\s*=\s*yes", config):
        return True, "dnf-automatic applies updates"
    return False, "security updates are not applied automatically"


def check_security_updates_pending(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    lines = _lines(facts.get("security_updates"))
    if "refresh=failed" in lines:
        # A failed refresh means the count would come from an old index, so it is not reported as a result
        return None, "package index could not be refreshed, so pending updates are unknown"
    lines = [line for line in lines if not line.startswith("refresh=")]
    try:
        count = int(lines[0])
    except (IndexError, ValueError):
        return None, "could not count security updates"
    return count == 0, f"{count} security update(s) pending"


def check_reboot_not_pending(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    value = _lines(facts.get("reboot_pending"))
    if not value:
        return None, "reboot state unknown"
    pending = value[0] == "yes"
    return not pending, "reboot pending" if pending else "no reboot pending"


def check_audit_logging(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    active = "active" in _lines(facts.get("audit"))
    return active, "auditd or rsyslog active" if active else "neither auditd nor rsyslog is active"


def check_time_sync(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    value = _lines(facts.get("time_sync"))
    if not value:
        return None, "clock sync state unknown"
    synced = value[0].lower() == "yes"
    return synced, "clock synchronised" if synced else "clock not synchronised"


def check_recent_patch_job(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    last = db.get("last_successful_patch")
    if not last:
        return False, "no successful patch job on record"
    days = (datetime.utcnow() - last).days
    return days <= PATCH_RECENCY_DAYS, f"last successful patch job {days} day(s) ago"


def check_no_failed_patch_job(facts: Dict[str, str], db: Dict[str, Any]) -> Tuple[Optional[bool], str]:
    latest = db.get("latest_execution_status")
    if latest is None:
        return None, "no patch job on record"
    return latest != "failed", f"latest patch job status: {latest}"


CHECKS: Dict[str, Tuple[str, Callable]] = {
    "os_supported": ("Operating system within vendor support window", check_os_supported),
    "ssh_root_disabled": ("SSH root login disabled", check_ssh_root_disabled),
    "ssh_password_disabled": ("SSH password authentication disabled", check_ssh_password_disabled),
    "password_max_age": ("Password expiry set to 90 days or less", check_password_max_age),
    "firewall_active": ("Host firewall active", check_firewall_active),
    "auto_security_updates": ("Automatic security updates enabled", check_auto_security_updates),
    "security_updates_pending": ("No pending security updates", check_security_updates_pending),
    "reboot_not_pending": ("No reboot pending after patching", check_reboot_not_pending),
    "audit_logging": ("Audit or system logging active", check_audit_logging),
    "time_sync": ("Clock synchronised (NTP)", check_time_sync),
    "recent_patch_job": ("Successful patch job within 30 days", check_recent_patch_job),
    "no_failed_patch_job": ("Latest patch job did not fail", check_no_failed_patch_job),
}

# framework id -> name, and requirements as (requirement id, title, check ids)
# Review status of the control mappings. Titles were checked against published summaries of each framework.
# The official texts are licensed, so wording should still be confirmed against them. Every mapping
# also needs an auditor's sign-off before a report is used as evidence.
MAPPING_STATUS = "Mapping not yet validated by an auditor"

# Notes on mappings that are judgement calls, or where the title is a paraphrase
MAPPING_NOTES: Dict[Tuple[str, str], str] = {
    ("hipaa", "164.312(e)(1)"): "Transmission security is about encrypting data in transit. A host firewall is a judgement call here.",
    ("hipaa", "164.308(a)(1)(ii)(B)"): "Risk management is covered by vulnerability and patch evidence. The mapping is a judgement call.",
    ("hipaa", "164.308(a)(5)(ii)(B)"): "Protection from malicious software is evidenced by automatic security updates. This is a proxy.",
    ("pci_dss", "8.2"): "Checked by the absence of direct root login. Full user identification needs process evidence too.",
    ("pci_dss", "8.3.1"): "Checked by whether password login is disabled. Other authentication methods are not assessed.",
    ("pci_dss", "1.4"): "Checked by the host firewall only. Network-level controls are not assessed.",
    ("soc2", "CC6.6"): "Checked by the host firewall only. Boundary controls outside the host are not assessed.",
    ("soc2", "CC8.1"): "Change management is evidenced by patch job history, not by a change approval process.",
    ("soc2", "CC7.4"): "Incident response is not assessed. This only checks reboot state and patch job outcome.",
    ("iso27001", "A.8.32"): "Change management is evidenced by patch job history, not by a change approval process.",
    ("cis", "7.3"): "Checked by whether automatic security updates are configured, not by whether they run successfully.",
}


FRAMEWORKS: Dict[str, Dict[str, Any]] = {
    "cis": {
        "name": "CIS Controls v8",
        "requirements": [
            ("2.2", "Ensure authorized software is currently supported", ["os_supported"]),
            ("4.1", "Establish and maintain a secure configuration process", ["ssh_root_disabled", "ssh_password_disabled", "password_max_age"]),
            ("4.4", "Implement and manage a firewall on servers", ["firewall_active"]),
            ("7.1", "Establish and maintain a vulnerability management process", ["security_updates_pending", "recent_patch_job"]),
            ("7.3", "Perform automated operating system patch management", ["auto_security_updates"]),
            ("8.2", "Collect audit logs", ["audit_logging"]),
            ("8.4", "Standardize time synchronization", ["time_sync"]),
        ],
    },
    "pci_dss": {
        "name": "PCI DSS v4.0",
        "requirements": [
            ("1.4", "Network connections between trusted and untrusted networks are controlled", ["firewall_active"]),
            ("6.3.1", "Security vulnerabilities are identified and managed", ["security_updates_pending"]),
            ("6.3.3", "Critical security patches installed within one month", ["security_updates_pending", "recent_patch_job"]),
            ("8.2", "Individual user identification (no shared root access)", ["ssh_root_disabled"]),
            ("8.3.1", "Strong authentication for access", ["ssh_password_disabled"]),
            ("8.3.9", "Passwords changed at least every 90 days", ["password_max_age"]),
            ("10.2", "Audit logs record system activity", ["audit_logging"]),
            ("10.6", "Time synchronisation for logs", ["time_sync"]),
            ("12.3.4", "Hardware and software technologies are reviewed at least every 12 months", ["os_supported"]),
        ],
    },
    "hipaa": {
        "name": "HIPAA Security Rule",
        "requirements": [
            ("164.308(a)(1)(ii)(B)", "Risk management (vulnerabilities and patching)",
             ["security_updates_pending", "recent_patch_job", "no_failed_patch_job"]),
            ("164.308(a)(5)(ii)(B)", "Protection from malicious software", ["auto_security_updates"]),
            ("164.312(a)(1)", "Access control", ["ssh_root_disabled", "ssh_password_disabled"]),
            ("164.312(b)", "Audit controls", ["audit_logging"]),
            ("164.312(d)", "Person or entity authentication", ["password_max_age", "ssh_password_disabled"]),
            ("164.312(e)(1)", "Transmission security (network controls)", ["firewall_active"]),
        ],
    },
    "soc2": {
        "name": "SOC 2 (Trust Services Criteria)",
        "requirements": [
            ("CC6.1", "Logical access security", ["ssh_root_disabled", "ssh_password_disabled"]),
            ("CC6.6", "Boundary protection", ["firewall_active"]),
            ("CC6.8", "Prevention of unauthorised or malicious software", ["auto_security_updates"]),
            ("CC7.1", "Vulnerability detection and monitoring", ["security_updates_pending"]),
            ("CC7.2", "Monitoring of system components", ["audit_logging", "time_sync"]),
            ("CC7.4", "Incident response and remediation", ["reboot_not_pending", "recent_patch_job"]),
            ("CC8.1", "Change management", ["no_failed_patch_job", "recent_patch_job"]),
        ],
    },
    "iso27001": {
        "name": "ISO/IEC 27001:2022 Annex A",
        "requirements": [
            ("A.5.19", "Information security in supplier relationships", ["os_supported"]),
            ("A.8.5", "Secure authentication", ["ssh_password_disabled", "password_max_age"]),
            ("A.8.8", "Management of technical vulnerabilities", ["security_updates_pending", "recent_patch_job"]),
            ("A.8.9", "Configuration management", ["ssh_root_disabled", "firewall_active"]),
            ("A.8.15", "Logging", ["audit_logging"]),
            ("A.8.17", "Clock synchronisation", ["time_sync"]),
            ("A.8.20", "Network security", ["firewall_active"]),
            ("A.8.32", "Change management", ["no_failed_patch_job", "reboot_not_pending"]),
        ],
    },
    "nist_csf": {
        "name": "NIST Cybersecurity Framework 2.0",
        "requirements": [
            ("PR.AA-05", "Access permissions managed", ["ssh_root_disabled", "ssh_password_disabled"]),
            ("PR.PS-01", "Configuration management practices", ["password_max_age", "firewall_active"]),
            ("PR.PS-02", "Software maintained and supported", ["os_supported", "security_updates_pending"]),
            ("PR.IR-01", "Networks protected from unauthorised access", ["firewall_active"]),
            ("ID.RA-01", "Vulnerabilities identified in assets", ["security_updates_pending"]),
            ("DE.CM-09", "Monitoring of computing hardware and software", ["audit_logging", "time_sync"]),
        ],
    },
}


def evaluate_host(facts: Dict[str, str], db: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for check_id, (title, fn) in CHECKS.items():
        try:
            passed, detail = fn(facts, db)
        except Exception as e:
            passed, detail = None, f"check error: {e}"
        results[check_id] = {"title": title, "passed": passed, "detail": detail}
    return results


def score_host(checks: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    frameworks: Dict[str, Dict[str, Any]] = {}
    for fw_id, fw in FRAMEWORKS.items():
        requirements = []
        passed = failed = not_assessed = 0
        for req_id, title, check_ids in fw["requirements"]:
            outcomes = [checks[c]["passed"] for c in check_ids]
            if any(o is False for o in outcomes):
                status = "failed"
                failed += 1
            elif all(o is True for o in outcomes):
                status = "passed"
                passed += 1
            else:
                status = "not_assessed"
                not_assessed += 1
            requirements.append({
                "id": req_id,
                "title": title,
                "status": status,
                "failed_checks": [c for c in check_ids if checks[c]["passed"] is False],
            })
        frameworks[fw_id] = {
            "name": fw["name"],
            "requirements": requirements,
            "passed": passed,
            "failed": failed,
            "not_assessed": not_assessed,
            "score": round(100 * passed / (passed + failed), 1) if passed + failed else None,
        }
    return frameworks


def fleet_summary(hosts: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    summary: Dict[str, Dict[str, Any]] = {}
    assessed = [h for h in hosts if h.get("status") == "assessed"]
    for fw_id, fw in FRAMEWORKS.items():
        passed = sum(h["frameworks"][fw_id]["passed"] for h in assessed)
        failed = sum(h["frameworks"][fw_id]["failed"] for h in assessed)
        summary[fw_id] = {
            "name": fw["name"],
            "passed": passed,
            "failed": failed,
            "score": round(100 * passed / (passed + failed), 1) if passed + failed else None,
        }
    return summary


def build_results(hosts: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "hosts": hosts,
        "summary": fleet_summary(hosts),
        "assessed_hosts": sum(1 for h in hosts if h.get("status") == "assessed"),
        "total_hosts": len(hosts),
    }


def _framework_ids(scope: str) -> List[str]:
    return list(FRAMEWORKS) if scope in ("", "all") else [scope]


def mapping_catalogue() -> Dict[str, Any]:
    """Every framework requirement with the checks behind it and any review note. For auditors."""
    frameworks = []
    for fw_id, fw in FRAMEWORKS.items():
        requirements = []
        for req_id, title, check_ids in fw["requirements"]:
            requirements.append({
                "id": req_id,
                "title": title,
                "checks": [{"id": c, "title": CHECKS[c][0]} for c in check_ids],
                "note": MAPPING_NOTES.get((fw_id, req_id)),
            })
        frameworks.append({"id": fw_id, "name": fw["name"], "status": MAPPING_STATUS, "requirements": requirements})
    return {"status": MAPPING_STATUS, "frameworks": frameworks}


def build_csv(results: Dict[str, Any], scope: str) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["framework", "requirement_id", "requirement_title", "hostname", "ip_address",
                     "requirement_status", "failed_checks", "details", "mapping_note"])
    for host in results.get("hosts", []):
        if host.get("status") != "assessed":
            writer.writerow(["all", "", "", host.get("hostname"), host.get("ip"), host.get("status"), "", host.get("detail", "")])
            continue
        for fw_id in _framework_ids(scope):
            for req in host["frameworks"][fw_id]["requirements"]:
                details = "; ".join(f"{c}: {host['checks'][c]['detail']}" for c in req["failed_checks"])
                writer.writerow([FRAMEWORKS[fw_id]["name"], req["id"], req["title"], host["hostname"], host["ip"],
                                 req["status"], ";".join(req["failed_checks"]), details,
                                 MAPPING_NOTES.get((fw_id, req["id"]), "")])
    return buffer.getvalue().encode("utf-8")


def build_report_lines(results: Dict[str, Any], scope: str) -> List[str]:
    lines = [
        "Compliance Assessment Report",
        f"Generated (UTC): {results.get('generated_at', '')}",
        f"Hosts assessed: {results.get('assessed_hosts', 0)} of {results.get('total_hosts', 0)}",
        "",
        "Framework scores (fleet)",
    ]
    for fw_id in _framework_ids(scope):
        s = results["summary"][fw_id]
        score = f"{s['score']}%" if s["score"] is not None else "not assessed"
        lines.append(f"  {s['name']}: {score}  (passed {s['passed']}, failed {s['failed']})")
    lines.append("")
    lines.append("Per host")
    for host in results.get("hosts", []):
        if host.get("status") != "assessed":
            lines.append(f"  {host.get('hostname')} ({host.get('ip')}): {host.get('status')} - {host.get('detail', '')}")
            continue
        lines.append(f"  {host['hostname']} ({host['ip']}) - {host.get('detail', '')}")
        for fw_id in _framework_ids(scope):
            fw = host["frameworks"][fw_id]
            lines.append(f"    {fw['name']}: score {fw['score'] if fw['score'] is not None else 'n/a'}%")
            for req in fw["requirements"]:
                if req["status"] == "failed":
                    reasons = "; ".join(host["checks"][c]["detail"] for c in req["failed_checks"])
                    lines.append(f"      FAILED {req['id']} {req['title']}: {reasons}")
    lines.append("")
    lines.append(f"{MAPPING_STATUS}. Requirements marked with a note are judgement calls:")
    for fw_id in _framework_ids(scope):
        for req_id, _, _ in FRAMEWORKS[fw_id]["requirements"]:
            note = MAPPING_NOTES.get((fw_id, req_id))
            if note:
                lines.append(f"  {FRAMEWORKS[fw_id]['name']} {req_id}: {note}")
    wrapped: List[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, 110, subsequent_indent="    ") or [""])
    return wrapped


def _pdf_escape(text: str) -> bytes:
    data = text.encode("latin-1", "replace")
    return data.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def build_pdf(lines: List[str]) -> bytes:
    per_page = 60
    chunks = [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]
    count = len(chunks)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))

    objects: Dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, chunk in enumerate(chunks):
        page_no, content_no = 4 + 2 * i, 5 + 2 * i
        body = [b"BT /F1 9 Tf 12 TL 40 760 Td"]
        body += [b"(" + _pdf_escape(line) + b") Tj T*" for line in chunk]
        body.append(b"ET")
        stream = b"\n".join(body)
        objects[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_no} 0 R >>"
        ).encode()
        objects[content_no] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets: Dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"

    xref_pos = len(out)
    total = max(objects) + 1
    out += f"xref\n0 {total}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, total):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {total} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    return bytes(out)
