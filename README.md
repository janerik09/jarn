# JÁRN

JÁRN manages patching for Linux servers. It keeps an inventory of your servers, runs patch jobs with approval steps
and staged rollouts, can back up servers before risky changes, checks compliance against common frameworks, and
keeps an audit trail of what happened and who did it. Backups are optional; see [Backups](#backups).

Start with [GETTING_STARTED.md](GETTING_STARTED.md) if you are installing it. These guides cover the rest:

- [RECOVERY.md](RECOVERY.md): restoring after a failure, keys, and how to test your restore
- [SSO.md](SSO.md): signing in with Entra ID, Okta, Google, Keycloak, or Active Directory and LDAP
- [NETWORK_ACCESS.md](NETWORK_ACCESS.md): ports, HTTPS, and firewalls
- [DEVELOPMENT.md](DEVELOPMENT.md): the development setup, tests and database migrations
- [CONTRIBUTING.md](CONTRIBUTING.md): how to send a change
- [SECURITY.md](SECURITY.md): how to report a vulnerability privately

## What it does

- **Inventory:** servers reached over SSH, with health, connectivity and the number of pending updates.
- **Patch jobs:** a five-step wizard covering servers, patches, deployment strategy, schedule and review. Canary and
  staged rollouts pause for review. Failed servers can be retried without touching the ones that succeeded.
- **Safety checks:** critical and kernel updates can require approval. Health checks run before and after patching,
  and a failed check triggers an automatic rollback. Application profiles, including Home Assistant Supervised, add
  their own checks.
- **Distribution upgrades:** these need a backup source configured first, a Proxmox target or a file target.
- **Schedules:** recurring patch jobs and a nightly compliance run, both respecting maintenance windows.
- **Compliance:** assessments against CIS, PCI DSS, HIPAA, SOC 2, ISO 27001 and NIST CSF. Reports export as PDF or
  CSV, and each one keeps its score history.
- **Backups (optional):** back up a server before patching, and restore it if the patch can't be rolled back. Choose
  the source that fits each server: a file backup of selected paths, or a whole Proxmox VM. Patch jobs work without
  either. See [Backups](#backups).
- **Off-host copy:** database dumps and audit archives can be copied to another server over SFTP.
- **Audit trail:** changes, sign-ins, refused actions and job steps, in a hash chain, with search, export and a
  timeline for each job.
- **Access:** three roles (viewer, operator, admin), optional authenticator-app MFA, and account lockout.
- **Single sign-on:** OpenID Connect providers and Active Directory or LDAP. See [SSO.md](SSO.md).
- **Notifications:** by webhook, or by email through Gmail, Outlook, Yahoo or your own SMTP server.
- **Operations:** a health endpoint, Prometheus metrics, alert rules, nightly database backups with a weekly restore
  check, and a watchdog for jobs that stop responding.

## Scope

JÁRN manages Linux servers reached over SSH. That covers Debian, Ubuntu, RHEL, CentOS and Fedora, using apt, yum or
dnf. Windows servers are not supported. The WinRM connector was removed.

## Patch jobs

### How a run is organised

A job lists its servers and patches. The deployment strategy decides how the servers are grouped into waves:

- **All at once** (the default): one wave with every server.
- **Canary:** the first wave is 10% of the servers, and you can change that. If the canary finishes without failures,
  the rollout pauses and sends a notification. Check the canary results, then press **Continue** (or call
  `POST /jobs/{id}/continue`) to run the rest.
- **Staged:** waves at 25%, 50% and 100% of the servers.

Kubernetes nodes are the exception. They run one at a time, in name order, and each one is checked before the next
starts.

Servers in a wave run at the same time, up to the maximum set in Admin Settings (5 by default).

### What happens on each server

1. A pre-patch health check runs. If it fails, the server is left alone.
2. JÁRN records the installed package versions and checks that they can be restored. If they can't, the job
   says so before patching starts.
3. The patches are installed. If the server reboots, JÁRN waits for it to come back and checks its services and
   kernel.
4. A post-patch health check runs.
5. If the install or the post-patch check fails and a rollback is possible, the old package versions are restored.
   If that also fails, the server is marked as needing a restore. If the job was set to back up first and restore on
   failure, JÁRN restores the server from that backup (see [Backups](#backups)).

Each server ends as `success`, `failed`, `rolled_back` or `skipped`. A `rolled_back` server was patched, the patch was
reverted automatically, and it is back where it started. A `failed` server with a failed rollback keeps the error
message that explains what went wrong.

### When something fails

- A failed server doesn't stop the others in its wave.
- If any server in a wave fails, the later waves don't start. Their servers are marked `skipped`, and the reason is
  recorded.
- A failed Kubernetes node skips the nodes after it.
- A job finishes as `completed` even when some servers failed. The failure count is in the job summary, in the
  `job.complete` audit event, and in the notification. Check each server before you call a job a success.

**Retry failed servers** creates a new job with only the servers that failed or were rolled back, using the same
patches. The original job's successful servers are left alone.

JÁRN retries SSH connections up to three times, waiting 10 and then 30 seconds between tries. It does this before
anything on the server changes. It never retries the patching itself, because a half-finished install is not safe to
run again.

### One run per server

A server can only be in one patch run at a time. If you start a job that includes a server a running job is already
patching, the start is refused. A job that has already started can't be started again, so a double click does
nothing extra. Draft jobs that haven't started don't hold their servers. If a draft is started later, the per-server
lock still stops an overlap.

### Rolling back a job

On a finished job, **Roll back job** shows a preview first. It lists the servers that will change, the packages and
their previous versions, and the servers that won't change and why. Confirming starts the rollback in the background.

- Only packages the run changed are downgraded, back to the versions recorded before it. Kernel packages (`linux-*`)
  stay as they are, since they only take effect after a reboot.
- Reboots are not undone.
- A server is skipped when the run was from before previous versions were recorded, when a previous version is no
  longer in any repository, when the run was a dry run, or when it has already been rolled back.
- If a rollback fails, the server keeps its patches, and the job records which servers failed.

### Job states

| Status | Meaning |
|---|---|
| `pending` | Created, not started |
| `running` | Servers are being patched, or the rollout is waiting for a canary review |
| `completed` | The run finished. The per-server results show what worked |
| `rolled_back` | Every problem in the run was reverted |
| `failed` | The run couldn't finish, for example because the watchdog stopped it |
| `cancelled` | A user cancelled it |

### Watchdog

A job still `running` after 200 minutes is failed by the watchdog. A job paused for canary review is exempt. Any
server still `pending` or `executing` is marked `failed` with the message "Stopped by the watchdog", and a
`job_stuck` notification is sent. Those servers may be partly patched, so check them before you retry.

### Pre-flight checks

Pre-flight checks each server before a job starts. You can run it from the **Pre-flight check** button on the job's
monitor page, or call `POST /api/v1/jobs/{id}/preflight-check`. It checks:

- connectivity and SSH login (blocking if either fails)
- a supported package manager, apt, yum or dnf (blocking)
- at least 1 GB of free disk space (blocking)
- whether the package manager is locked, and whether sudo asks for a password (warnings only)

## Configuration

Secrets go in `.env` in the project root. That file is git-ignored, so keep a copy somewhere safe. If you lose
`CREDENTIAL_ENCRYPTION_KEY`, the stored SSH keys, passwords and backup tokens can't be decrypted.

| Variable | Required | What it does |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | The database password |
| `SECRET_KEY` | yes | Signs the access tokens. Use at least 32 random characters. In production the app won't start with the default. Changing it signs everyone out. |
| `CREDENTIAL_ENCRYPTION_KEY` | yes | 32 random bytes, base64 encoded. Encrypts SSH keys, passwords and the backup and off-host credentials. |
| `CORS_ORIGINS` | no | Origins allowed to call the API from another site. Empty by default, since the dashboard is served from the same address. |
| `METRICS_TOKEN` | no | If set, `/metrics` needs `Authorization: Bearer <token>`. |
| `AUDIT_RETENTION_DAYS` | no | Audit events older than this move to `backups/audit` and leave the live table. Default 365. |
| `BACKUP_KEEP_DAYS` | no | How many local database dumps to keep. Default 14. |
| `SESSION_IDLE_MINUTES` | no | Sign-in ends after this long without activity. Default 60. |
| `SESSION_MAX_HOURS` | no | Sign-in ends this many hours after signing in, even if in use. Default 12. |

Generate the three secrets with:

```bash
openssl rand -hex 32                                                       # SECRET_KEY
python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"   # CREDENTIAL_ENCRYPTION_KEY
openssl rand -base64 24                                                    # POSTGRES_PASSWORD
```

Everything else, including backup targets, notifications, web access, the off-host copy and users, is set in
**Admin Settings** in the dashboard. Secrets entered there are stored encrypted.

## Architecture

| Container | What it does |
|---|---|
| `web` | nginx. Serves the dashboard, forwards `/api` and `/health`, and handles HTTPS when it's turned on. Ports 80 and 443. |
| `backend` | The FastAPI API under `/api/v1`, on internal port 8000. |
| `worker` | The Celery worker. Runs patch jobs, backups, scans and scheduled work. |
| `beat` | The Celery scheduler. |
| `postgres` | PostgreSQL 16. Alembic manages the schema, and migrations run when the backend starts. |
| `redis` | The Celery broker, plus the rate-limit and server-lock counters. |
| `db-backup` | Nightly database dumps into `./backups`, with a check on each one and a restore test each week. |

## Quick start

```bash
cp .env.example .env          # then fill in the three secrets
docker compose up -d --build
```

Open `http://<host>/`. The first visit asks you to create the administrator account. See
[GETTING_STARTED.md](GETTING_STARTED.md) for the full steps.

## Security

- **Passwords** are hashed with bcrypt. There is no minimum length yet. That was a deliberate choice, and a policy
  will come later.
- **Sign-in:** an account locks for 15 minutes after 10 failed attempts. An address can make 30 sign-in attempts
  every 15 minutes, and the count is shared across all backend containers through Redis.
- **MFA** is optional. It uses standard time-based codes (RFC 6238, SHA-1, 6 digits, 30 seconds), so Google
  Authenticator, Microsoft Authenticator and most other apps work. Ten single-use recovery codes are shown once.
- **Sessions:** the access token lasts 15 minutes and stays in the page's memory. The refresh token is an HttpOnly,
  SameSite=Strict cookie limited to `/api/v1/auth`, and each refresh replaces it. If someone replays a token that has
  already been replaced, every session for that user is signed out. Two tabs refreshing at the same moment get a
  30-second grace period, so they don't trigger this.
- **Session limits:** a session ends after `SESSION_IDLE_MINUTES` without activity (default 60) and `SESSION_MAX_HOURS`
  after signing in (default 12). Activity means clicks, keys or scrolling in the browser. A tab left open signs itself out
  after the idle period. Reopening the dashboard the next morning asks for a sign-in.
- **Roles:** viewers can read. Operators can run jobs and change servers. Admins can change settings, users and
  backups.
- **Encryption at rest:** credentials and TLS keys are sealed with AES-256-GCM.
- **Managed servers:** JÁRN only connects to a server with the SSH host key an administrator approved for it. An
  unapproved or changed key blocks the connection, so credentials never go to a machine that hasn't been verified.
- **Metrics** are not on the public port. Prometheus reaches the backend on the internal network.
- **Off-host copy:** the same host key rule applies. Nothing is sent until you approve the key. Without an off-host
  copy, the dashboard shows a warning, and the alert stays open until someone records a waiver with a reason.

## Backups

Backups are optional. JÁRN never needs one to patch a server, and you can use none, one or both kinds below. Pick the
source for each server in **Admin Settings → Backup → Server backup sources**. Two things need a source: a patch job
that is set to back up first, and a distribution upgrade, which won't start without one.

JÁRN only starts a server backup when you click **Back up now**, or when a patch job or distribution upgrade asks for
one. It never schedules server backups. The only scheduled backup is the nightly off-host copy of JÁRN's own database
and audit archives. Your own backup software or Proxmox's backup jobs are separate from JÁRN.

### File backups to a share (NFS)

For servers that have no backup solution, or that aren't virtual machines at all. JÁRN copies each server's chosen
paths over SSH into a folder on the JÁRN host, normally an NFS export mounted there. Nothing needs to be installed on
the managed server beyond `tar`.

1. Export a share from your NAS to the JÁRN host only. Mount it on the host at a fixed path, for example
   `/mnt/jarn-backups`, and add it to `/etc/fstab` so it survives a reboot.
2. In `docker-compose.yml`, uncomment the `/mnt/jarn-backups` lines under `backend` and `worker`, then restart the stack.
3. In **Admin Settings → Backup**, add a **file backup target**. Set the folder, how many backups to keep per server,
   and the paths to back up. Leave the paths empty to use `/etc`, `/boot`, `/root`, `/home`, `/srv`, `/opt` and
   `/usr/local`. Then choose **Test all connections**.
4. Under **Server backup sources**, map each server to that file target.

- The SSH login and `sudo` for each server are the ones JÁRN already uses for it. `tar` runs under `sudo`, so the
  account needs that permission, with or without a password.
- The folder must be a mounted share. If it isn't, the backup stops instead of filling this host's disk. Turn off
  **Require the folder to be a mounted share** only for a local folder.
- Each backup is written to a temporary file, checked as a valid gzip, and then given a SHA-256 checksum in a manifest.
  Older backups are removed only after a new one has been verified, keeping the number you chose.
- **Restore** checks the archive against its checksum and the server's `/var/tmp` for enough room. It copies the archive to
  `/var/tmp` on the server, checks the checksum there, extracts it, and removes the copy. Nothing is extracted if a check fails.
- A file restore puts back only the backed-up paths. It does not roll back packages or the kernel, and files created after
  the backup are left in place. For a failed kernel update, use the server's own boot or snapshot tools.
- Backups contain password hashes and private keys. Export the share to the JÁRN host only, and keep it off any
  network that other people can read.

### Proxmox VE (for virtual machines and containers)

Use this when a server is a Proxmox VM or container and you want the whole machine restored, not just files. Add a
Proxmox target in **Admin Settings → Backup**, using an API token. The token needs the permissions the backup and
restore calls use. Restores also need `Datastore.AllocateSpace` on each storage they write to, and `SDN.Use` on the
bridge the VM uses. The restore check reports any permission that's missing. Then map each server to its VM or
container. JÁRN works with any storage the Proxmox backup jobs write to; Proxmox Backup Server is one option.

- The storage for each VM comes from the enabled Datacenter backup job that covers it.
- **Restore** runs a check before anything is stopped. It confirms the backup exists and can be read. It confirms each
  disk's storage exists, is active, accepts guest disks and has enough room. The current VM's disks count as free
  space, since Proxmox replaces them. It also confirms the token can allocate space there.
  Each disk goes back to its original storage by default. You can choose one storage for every disk instead.
  If a restore fails after it has started, the VM stays stopped. JÁRN won't start it again on its own.

### Database

The `db-backup` container writes a dump to `./backups` every day and checks that it can be read. Once a week it
restores the newest dump into a scratch database, to prove the dump works.

### Off-host copy

Turn it on in **Admin Settings → Off-host copy**:

1. Enter the server, port, username, remote folder, and how many copies to keep.
2. Choose a key or a password. Either one is stored encrypted and never shown again.
3. Press **Test connection**. It writes and then removes a small test file, and it shows the server's key
   fingerprint. Compare that fingerprint with the one your NAS or server shows, and if they match, press
   **Approve this server key**.
4. Save. Every day at 03:30 UTC, any database dump or audit archive that isn't on the remote server gets uploaded.
   Each upload is checked by size before it's moved into place. Old copies are only removed after every upload
   succeeds, and the newest copies of each kind are kept. **Copy now** runs it straight away.

The copy only includes JÁRN's own database dumps and audit archives. Backups of managed servers are not included;
keep them where they are stored (a Proxmox server, or the file backup share).

## Audit trail

JÁRN records every change made through the API, and every 401, 403 and 429 response. It also records sign-in attempts,
SSH commands with their exit codes, credential use, job steps, Proxmox operations, and configuration changes. Secrets
show as changed but their values are never stored. System start and stop are recorded too.

Each event has a sequence number, a category, an outcome, the actor, the source address, the resource, a reason, and
a hash linking it to the event before. Events from one job share a correlation id.

In **Audit Logs** you can search and filter, open the details of any event, show every event for one job, run an
integrity check, and export as CSV or JSON. Export is for administrators.

Events older than the retention period move to `backups/audit/*.jsonl.gz` and leave the live table. The integrity check
covers events written since the hash chain was added. Earlier events are kept, but they can't be verified.

## Operations

- **Health:** `GET /health` reports the status of the database, Redis and the worker. A 503 means something is down.
- **Metrics:** `GET /metrics` returns Prometheus data. To run the optional Prometheus setup, use
  `docker compose --profile monitoring up -d prometheus`.
- **Stuck jobs** are failed automatically after 200 minutes (see [Watchdog](#watchdog)).
- **Package scans:** servers are rescanned every hour if their last scan is older than the scan interval. The default
  interval is 6 hours. Set `scan_interval_hours` to 0 in the general settings to turn scheduled scans off.
- **Reset an administrator's password** from the server:

  ```bash
  docker compose exec backend python -m app.cli reset-password <username>
  ```

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md). The production image doesn't include the test tools, so run the tests from the
development setup.

## Licence and support

JÁRN is free software under the [Apache License 2.0](LICENSE). You can use, change and redistribute it, including in
closed-source products, as long as you keep the licence and copyright notices.
