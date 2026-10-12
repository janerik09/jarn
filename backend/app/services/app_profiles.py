"""
Application profiles: how to recognise an application on a server, which packages must not be
upgraded routinely, and how to tell whether it is healthy before and after a patch.

Detection is evidence-based: systemd units, container names and images, and files on disk.
Health commands must exit 0 when the application is healthy. A check that passes before a patch and
fails after it is a regression. A check that fails before a patch blocks the patch.
"""

from dataclasses import dataclass, field
from typing import List


@dataclass(frozen=True)
class AppProfile:
    id: str
    name: str
    detect_units: List[str] = field(default_factory=list)
    detect_container_names: List[str] = field(default_factory=list)
    detect_container_images: List[str] = field(default_factory=list)
    detect_paths: List[str] = field(default_factory=list)
    companion_units: List[str] = field(default_factory=list)  # Part of the application, not used for detection
    hold_packages: List[str] = field(default_factory=list)
    health_commands: List[str] = field(default_factory=list)
    drain_before_patch: bool = False
    containerized: bool = False  # Runs in Docker; host package changes affect it only via the Docker daemon
    guidance: str = ""  # Shown with the warning when a job affects this application


PROFILES: List[AppProfile] = [
    AppProfile(
        id="kubernetes_node",
        name="Kubernetes node",
        detect_units=["kubelet.service"],
        companion_units=["containerd.service"],
        hold_packages=["kubelet", "kubeadm", "kubectl", "containerd", "containerd.io", "cri-tools"],
        health_commands=[
            "systemctl is-active --quiet kubelet",
            "systemctl is-active --quiet containerd",
            "curl -sf -m 5 -o /dev/null http://127.0.0.1:10248/healthz",
        ],
        drain_before_patch=True,
    ),
    AppProfile(
        id="docker",
        name="Docker",
        detect_units=["docker.service"],
        hold_packages=["docker-ce", "docker.io"],
        health_commands=[
            "systemctl is-active --quiet docker",
            "sudo docker info >/dev/null 2>&1",
        ],
    ),
    AppProfile(
        id="home_assistant",
        name="Home Assistant (Supervised)",
        detect_units=["hassio-supervisor.service"],
        companion_units=["haos-agent.service", "hassio-apparmor.service"],
        detect_container_names=["homeassistant", "hassio_supervisor"],
        detect_container_images=["home-assistant/"],
        detect_paths=["/usr/share/hassio/homeassistant"],
        hold_packages=["homeassistant-supervised", "os-agent"],
        health_commands=[
            "systemctl is-active --quiet hassio-supervisor",
            "systemctl is-active --quiet haos-agent",
            "sudo docker inspect --format '{{.State.Status}}' homeassistant | grep -qx running",
            "curl -sf -m 10 -o /dev/null http://127.0.0.1:8123/",
        ],
        containerized=True,
        guidance="Take a full backup in Home Assistant (Settings > System > Backups) first. Core upgrades are "
                 "managed by Supervisor, not apt, so this job does not change the Home Assistant core. A Docker or "
                 "containerd upgrade restarts every container, which stops automations until the core is back. "
                 "Keep console access in case NetworkManager restarts. homeassistant-supervised and os-agent are "
                 "held out of routine jobs.",
    ),
    AppProfile(
        id="nginx",
        name="nginx (host service)",
        detect_units=["nginx.service"],
        health_commands=[
            "sudo nginx -t >/dev/null 2>&1",
            "systemctl is-active --quiet nginx",
        ],
    ),
    AppProfile(
        id="npm_plus",
        name="Nginx Proxy Manager Plus",
        detect_container_images=["nginx-proxy-manager", "npmplus", "npm-plus"],
        detect_container_names=["npmplus", "nginx-proxy-manager", "npm-plus"],
        containerized=True,
        health_commands=[
            "curl -sf -m 10 -o /dev/null http://127.0.0.1:81/api/ || curl -skf -m 10 -o /dev/null https://127.0.0.1:81/api/",
        ],
    ),
    AppProfile(
        id="harbor",
        name="Harbor registry",
        detect_container_names=["harbor-"],
        detect_container_images=["goharbor/"],
        detect_paths=["/opt/harbor/harbor.yml"],
        containerized=True,
        health_commands=[
            "sudo docker inspect --format '{{.State.Health.Status}}' harbor-core | grep -qx healthy",
            "sudo docker inspect --format '{{.State.Health.Status}}' harbor-db | grep -qx healthy",
            "curl -skf -m 10 -o /dev/null https://127.0.0.1:443/api/v2.0/health",
        ],
    ),
    AppProfile(
        id="gitlab",
        name="GitLab (self-hosted, Omnibus)",
        detect_units=["gitlab-runsvdir.service"],
        detect_paths=["/etc/gitlab/gitlab.rb", "/opt/gitlab/version-manifest.json"],
        detect_container_images=["gitlab/gitlab-ce", "gitlab/gitlab-ee"],
        hold_packages=["gitlab-ce", "gitlab-ee"],
        health_commands=[
            "systemctl is-active --quiet gitlab-runsvdir",
            "! sudo gitlab-ctl status 2>/dev/null | grep -q '^down'",
            "curl -sf -m 15 -o /dev/null http://127.0.0.1/-/readiness",
        ],
        guidance="Back up first with 'sudo gitlab-backup create'. Omnibus upgrades run database migrations and must follow "
                 "GitLab's upgrade path, so gitlab-ce and gitlab-ee are held out of routine jobs.",
    ),
    AppProfile(
        id="vault",
        name="HashiCorp Vault",
        detect_units=["vault.service"],
        detect_paths=["/etc/vault.d/vault.hcl"],
        hold_packages=["vault"],
        health_commands=[
            "systemctl is-active --quiet vault.service",
            """ADDR=$(sudo awk -F'"' '/^[[:space:]]*address/ {print $2; exit}' /etc/vault.d/vault.hcl); CODE=$(curl -sk -m 5 -o /dev/null -w '%{http_code}' https://$ADDR/v1/sys/health 2>/dev/null); if [ "$CODE" = 000 ] || [ -z "$CODE" ]; then CODE=$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://$ADDR/v1/sys/health 2>/dev/null); fi; [ "$CODE" = 200 ] || [ "$CODE" = 429 ]""",
        ],
        guidance="Vault seals itself when it restarts, so a patch that restarts it needs an unseal afterwards. Take a "
                 "snapshot first with 'vault operator raft snapshot save' and keep the unseal keys available. "
                 "vault is held out of routine jobs.",
    ),
    AppProfile(
        id="technitium_dns",
        name="Technitium DNS server",
        detect_paths=["/opt/technitium/dns/DnsServerApp.dll"],
        companion_units=["dns.service"],
        health_commands=[
            "systemctl is-active --quiet dns.service",
            "curl -sf -m 5 -o /dev/null http://127.0.0.1:5380/",
            "dig +time=3 +tries=1 @127.0.0.1 . NS | grep -qE 'status: (NOERROR|REFUSED|NXDOMAIN)'",
        ],
        guidance="Technitium is a .NET application under /opt/technitium and keeps its configuration in /etc/dns. Back up "
                 "/etc/dns first, and patch one DNS server at a time so the other keeps answering queries.",
    ),
    AppProfile(
        id="postgresql",
        name="PostgreSQL",
        detect_units=["postgresql.service"],
        health_commands=[
            "sudo -u postgres psql -tAc 'select 1' >/dev/null 2>&1",
        ],
    ),
]

# OS services that are expected on any server; not reported as unknown applications.
BASELINE_UNITS = {
    "ssh.service", "sshd.service", "cron.service", "dbus.service", "rsyslog.service",
    "systemd-journald.service", "systemd-logind.service", "systemd-timesyncd.service",
    "systemd-udevd.service", "systemd-resolved.service", "systemd-networkd.service",
    "networking.service", "getty@tty1.service", "qemu-guest-agent.service", "user@1000.service",
    "rpcbind.service", "polkit.service", "unattended-upgrades.service", "apparmor.service",
    "chrony.service", "wpa_supplicant.service", "ModemManager.service",
}


def _container_match(profile: AppProfile, containers: List[tuple]) -> bool:
    for name, image in containers:
        lname, limage = name.lower(), image.lower()
        if any(n in lname for n in profile.detect_container_names):
            return True
        if any(i in limage for i in profile.detect_container_images):
            return True
    return False


def match_profiles(running_units: List[str], containers: List[tuple], existing_paths: List[str]) -> List[AppProfile]:
    """containers: list of (name, image) tuples for running containers."""
    matched = []
    for profile in PROFILES:
        if any(u in running_units for u in profile.detect_units):
            matched.append(profile)
        elif _container_match(profile, containers):
            matched.append(profile)
        elif any(path in existing_paths for path in profile.detect_paths):
            matched.append(profile)
    return matched
