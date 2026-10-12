# JÁRN — forged patching

JÁRN is a patch-management platform for Linux servers. It inventories servers, plans and runs patch jobs with
approval gates, maintenance windows and staged rollouts, takes Proxmox VE backups before risky changes, reports
compliance against common frameworks, and keeps a tamper-evident audit trail of everything that happens.

Start with [GETTING_STARTED.md](GETTING_STARTED.md) for installation and the first sign-in. The other guides:

- [RECOVERY.md](RECOVERY.md): restoring after a loss, keys, and the restore drill
- [SSO.md](SSO.md): signing in with Entra ID, Okta, Google, Keycloak, or Active Directory / LDAP

- [NETWORK_ACCESS.md](NETWORK_ACCESS.md): ports, HTTP and HTTPS, firewalls, and what is reachable from where
- [DEVELOPMENT.md](DEVELOPMENT.md): running the development overlay, tests, and database migrations

## What it does

- **Inventory**: servers over SSH with health, connectivity and pending-update counts, plus server detail pages.
- **Patch jobs**: a five-step wizard (servers, patch matrix, deployment strategy, schedule, review). Strategies
  include canary and staged rollouts that pause for review. Reboots are handled, and failed servers can be retried
  without touching the ones that succeeded.
- **Approvals and safety**: critical and kernel updates can require approval. Pre- and post-patch health checks run
  with automatic rollback. Application safety profiles (including Home Assistant Supervised) add guardrails.
- **Distribution upgrades**: release upgrades are gated and require a backup first.
- **Schedules**: recurring patch jobs and a nightly compliance run, respecting maintenance windows.
- **Compliance**: assessments against CIS, PCI DSS, HIPAA, SOC 2, ISO 27001 and NIST CSF, exported as PDF or CSV,
  with score history and attestation statements.
- **Backups**: Proxmox VE with a Proxmox Backup Server storage. Backups run only when you start them, from a patch
  job or the Backup button. Restores check the backup, the storage and the permissions before anything is stopped.
- **Off-host copy**: database dumps and audit archives can be copied to another server over SFTP.
- **Audit trail**: every change, sign-in, refused action and job step, in a hash chain, with search, export and a
  per-job timeline.
- **Access control**: three roles (viewer, operator, admin), optional authenticator-app MFA, and account lockout.
- **Notifications**: webhook, and email through Gmail, Outlook, Yahoo or a custom SMTP server.
- **Operations**: health endpoint, Prometheus metrics, alert rules, nightly database backups with a restore check,
  and a watchdog that fails jobs stuck past their time limit.

## Scope

JÁRN manages Linux servers reached over SSH (Debian, Ubuntu, RHEL, CentOS, Fedora and derivatives, using apt, yum
or dnf). Windows servers are not supported; the earlier WinRM connector was removed.

## Patch jobs

### How a run is organised

A job lists its servers and their patches. Servers run in **waves** set by the deployment strategy:

- **All at once** (default): one wave with every server.
- **Canary**: a first wave of 10% of the servers (configurable). When it finishes without failures the rollout
  pauses and a notification is sent. Review the canary results, then press **Continue** (`POST /jobs/{id}/continue`)
  to run the rest.
- **Staged**: waves at 25%, 50% and 100% of the servers.

Kubernetes nodes in a job are handled separately: one at a time, in name order, each checked for readiness before
the next starts.

### What happens on each server

1. Pre-patch health check. A failure stops that server before anything is changed.
2. The currently installed package versions are recorded, and the app checks whether those versions can be
   restored. If they cannot, the job says so before patching.
3. Patches are installed. If the server reboots, the app waits for it to come back and verifies services and
   kernel.
4. Post-patch health check.
5. If installation or the post-patch check fails and rollback is possible, the previous package versions are
   restored automatically. If that also fails, the server is marked as needing a restore from backup. A job can
   take a Proxmox backup first and restore on failure when that option is set (see [Backups](#backups)).

Each server ends as `success`, `failed`, `rolled_back` or `skipped`. A server whose automatic rollback worked is
`rolled_back`: its patches were reverted and it is back where it started. A server whose rollback did not work is
`failed`, and its error message says so.

### Failure handling

- A failed server does not stop the other servers in its wave.
- Once a wave has any failure, the later waves are not started. Their servers are marked `skipped` with the reason.
- A Kubernetes node failure skips the nodes after it.
- The job's status becomes `completed` when the run finishes, whatever the per-server results. The count of failed
  servers is in the job's summary, in the audit event `job.complete` (outcome `failure` when any server failed) and
  in the notification. Check each server's result before treating a job as successful.

Servers in a wave run at the same time, up to the **maximum servers at once** set in Admin Settings (default 5).

A server takes part in one patch run at a time. Starting a job that includes a server a running job is already
patching is refused. A job that has been started cannot be started again, so a double click does nothing extra.
Draft jobs that have not started do not hold their servers.

### Retrying

SSH connections are attempted up to three times, with 10 and 30 seconds between attempts. This happens before anything
on the server is changed. Patching itself is never retried automatically, because a half-finished install is not safe
to run again.

**Retry failed servers** creates a new job containing the servers that failed or were rolled back, with the same
patches. The original run's successful servers are not touched.

### Rolling back a job

On a finished job, **Roll back job** first shows a preview: the servers that will be changed, the packages and their
previous versions, and the servers that will not be changed and why. Confirming starts the rollback in the background.

- Only packages the run changed are downgraded, to the versions recorded before it ran. Kernel packages (`linux-*`)
  are left as installed. They take effect on reboot and are not downgraded by package.
- Reboots are not undone.
- A server is not rolled back when the run predates recording of previous versions, when a previous version is no
  longer offered by any repository, when the run was a dry run, or when it has already been rolled back.
- A server that rolls back is `rolled_back`. If a rollback fails, the server keeps its patches and the job records
  which servers failed to roll back.

### Job states

| Status | Meaning |
|---|---|
| `pending` | Created, not started |
| `running` | Servers are being patched, or the rollout is waiting for a canary review (shown in the job) |
| `completed` | The run finished. Per-server results show what succeeded |
| `failed` | The run could not finish, for example when the watchdog stopped it |
| `cancelled` | A user cancelled the job |

`rolled_back` is set when every problem in a run was reverted. If any server failed without a rollback, the job
stays `completed`.

### Watchdog

A job still `running` after 200 minutes is failed by the watchdog, unless it is paused for a canary review. Every
server still `pending` or `executing` is marked `failed` with the message "Stopped by the watchdog", the job is
recorded as `failed`, and a `job_stuck` notification is sent. Servers may have been partly patched, so check them
before retrying.

### Pre-flight checks

`POST /api/v1/jobs/{id}/preflight-check` checks each server before a job starts:

- connectivity and SSH authentication (blocking if they fail)
- a supported package manager (apt, yum or dnf) (blocking)
- at least 1 GB free disk space (blocking)
- the package manager is not locked, and sudo does not need a password (warnings only)

Pre-flight is a **Pre-flight check** button on the job's monitor page, and is also available through the API.

## Known limitations

- **Pre-flight is not in the dashboard.** The check runs only through the API.
- **Password policy.** No minimum length is enforced yet, by agreement.
- **Frontend tests.** There is no automated test runner for the dashboard; see [DEVELOPMENT.md](DEVELOPMENT.md).

| Container | Role |
|---|---|
| `web` | nginx. Serves the dashboard, proxies `/api` and `/health`, terminates TLS when enabled. Ports 80 and 443. |
| `backend` | FastAPI API under `/api/v1`. Internal port 8000. |
| `worker` | Celery worker. Runs patch jobs, backups, scans and scheduled work. |
| `beat` | Celery scheduler. |
| `postgres` | PostgreSQL 16. Schema is managed by Alembic and applied at backend start. |
| `redis` | Celery broker and rate-limit counters. |
| `db-backup` | Nightly database dump, verification and weekly restore check into `./backups`. |

## Quick start

```bash
cp .env.example .env          # then fill in the three secrets (see below)
docker compose up -d --build
```

Open `http://<host>/`. On first visit you create the administrator account. See
[GETTING_STARTED.md](GETTING_STARTED.md).

## Configuration

Secrets live in `.env` at the project root, which is git-ignored. Back this file up. Without
`CREDENTIAL_ENCRYPTION_KEY`, stored SSH keys, passwords and backup tokens cannot be decrypted.

| Variable | Required | Purpose |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | Database password |
| `SECRET_KEY` | yes | Signs access tokens. Use at least 32 random characters. In production the app refuses to start with the default. Changing it signs everyone out. |
| `CREDENTIAL_ENCRYPTION_KEY` | yes | Base64 of 32 random bytes (AES-256-GCM). Encrypts SSH keys, passwords, backup and off-host credentials at rest. |
| `CORS_ORIGINS` | no | Comma-separated origins allowed to call the API from another site. Empty by default; the dashboard is same-origin. |
| `METRICS_TOKEN` | no | If set, `/metrics` requires `Authorization: Bearer <token>`. |
| `AUDIT_RETENTION_DAYS` | no | Audit events older than this are archived to `backups/audit` and removed from the live table. Default 365. |
| `BACKUP_KEEP_DAYS` | no | Local database dumps kept. Default 14. |

Generate the secrets with:

```bash
openssl rand -hex 32                                                       # SECRET_KEY
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # CREDENTIAL_ENCRYPTION_KEY
openssl rand -base64 24                                                    # POSTGRES_PASSWORD
```

Everything else (Proxmox connection, notifications, web access, off-host copy, users) is configured in
**Admin Settings** in the dashboard and stored encrypted where it holds a secret.

## Security summary

- **Passwords**: bcrypt (SHA-256 pre-hash, so long passphrases are not truncated). No length rule is enforced yet.
- **Sign-in**: the account locks for 15 minutes after 10 failed attempts. Each address is limited to 30 sign-in
  attempts per 15 minutes, shared across all backend containers through Redis.
- **MFA**: optional per user, using standard time-based codes (RFC 6238, SHA-1, 6 digits, 30 seconds). Works with
  Google Authenticator, Microsoft Authenticator and other common apps. Ten single-use recovery codes are shown once.
- **Sessions**: the access token lasts 15 minutes and lives only in page memory. The refresh token is an HttpOnly,
  SameSite=Strict cookie limited to `/api/v1/auth`. Each refresh rotates it. Replaying a rotated token signs out
  every session of that user, except within a 30-second grace window for two tabs refreshing together.
- **Roles**: viewers read, operators run jobs and change servers, admins manage settings, users and backups.
- **Encryption at rest**: credentials and TLS keys are sealed with AES-256-GCM.
- **Audit**: see below. Sign-in failures, refused requests and credential use are recorded.
- **Metrics**: not published on the public port. Prometheus scrapes the backend on the internal network.
- **Managed servers**: a server is only connected to with the SSH host key an administrator approved for it. An unapproved
  or changed key blocks the connection, so credentials are never sent to an unverified machine.
- **Off-host copy**: connections are pinned to the server's SSH host key. Nothing is sent until you approve a key.
  Without an off-host copy the dashboard warns, and the alert stays open, unless an administrator records a waiver with a reason.

## Backups

### Proxmox VE (virtual machines and containers)

Add a Proxmox target in **Admin Settings → Backup** with an API token. The token needs the permissions the backup
and restore calls use. Restores also need `Datastore.AllocateSpace` on each storage they write to, and `SDN.Use`
on the bridge the VM uses. The restore check reports any permission that is missing. Map each server to its VM or
container.

- Backups are **manual**. The app never starts a Proxmox backup on a schedule. Your Proxmox backup jobs handle
  day-to-day backups; JÁRN only starts one when you click it or when a patch job asks for one.
- The storage for each VM comes from the enabled Datacenter backup job that covers it.
- **Restore** opens a check first. It confirms the backup exists and can be read, that each disk's storage exists,
  is active, accepts guest disks and has room (the current VM's disks count as free because Proxmox replaces them),
  and that the token can allocate space there. By default each disk returns to its original storage. You can pick
  one storage for every disk instead. Nothing is stopped until every check passes. If a restore fails after it
  starts, the VM is left stopped and is not started again automatically.

### Database backups

The `db-backup` container writes a dump to `./backups` each day, checks it can be read, and once a week restores it
into a scratch database to prove it works.

### Off-host copy

Turn this on in **Admin Settings → Off-host copy**:

1. Enter the server, port, username, remote folder and how many copies to keep.
2. Choose a key or password. Keys and passwords are stored encrypted and are not shown again.
3. Press **Test connection**. It writes and removes a small test file. Compare the fingerprint it shows with the
   fingerprint your NAS or server displays, then press **Approve this server key**.
4. Save. From then on, every day at 03:30 UTC, any database dump or audit archive missing from the remote folder is
   uploaded. Each upload is checked by size before it is renamed into place. Old copies are removed only after all
   uploads succeed, keeping the newest N of each kind. **Copy now** runs it immediately.

The copy only covers the platform's own database dumps and audit archives. Proxmox VM backups stay on Proxmox.

## Audit trail

Every mutating API call is recorded, along with every 401, 403 and 429 response, sign-in attempts, SSH commands and
their exit codes, credential use, job steps, Proxmox operations, configuration diffs (secrets are flagged as changed
but never stored), and system start and stop.

Each event carries a sequence number, category, outcome, actor, source address, resource, reason, and a hash that
chains it to the previous event. **Audit Logs** provides search and filters, a details view, a "show all events for
this job" action, an integrity check, and CSV or JSON export for administrators.

Each event records who did it (user name and id), what happened (action, category, outcome), the resource and its id,
the reason, the source address and browser (user agent), any configuration changes as before and after values,
and the correlation id that ties together every event of one job.

Events older than the retention period move to `backups/audit/*.jsonl.gz` and leave the live table. The chain check
covers events written since the chain was introduced. Events from before that are kept but cannot be verified.

## Operations

- Health: `GET /health` reports the database, Redis and worker status. HTTP 503 means degraded.
- Metrics: `GET /metrics` (Prometheus format). Start the optional monitoring profile with
  `docker compose --profile monitoring up -d prometheus`.
- Stuck jobs fail automatically after 200 minutes (see [Watchdog](#watchdog)).
- Server package scans: servers are rescanned hourly when their last scan is older than the scan interval (default
  6 hours; set `scan_interval_hours` to 0 in general settings to turn scheduled scans off).
- Administrator password reset from the server:

```bash
docker compose exec backend python -m app.cli reset-password <username>
```

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md). Backend tests run with:

```bash
docker exec -w /app patching-backend python -m pytest -q
```
