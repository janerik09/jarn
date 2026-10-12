# Development

## Layout

```
backend/
  app/
    api/v1/        HTTP routes, one module per area (auth, servers, jobs, backups, offsite, audit, ...)
    services/      Business logic: Proxmox provider, off-host copy, audit, notifications, TLS, ratelimit
    models/        SQLAlchemy models
    tasks.py       Celery tasks (patch jobs, backups, restores, scheduled work)
    celery_app.py  Celery configuration and the beat schedule
    security.py    Password hashing, tokens, and AES-256-GCM sealing of secrets
  migrations/      Alembic revisions, applied automatically at backend start
  tests/           pytest suite
frontend-web/
  src/components/  React screens (AdminSettings, ServerBackups, AuditLog, ...)
  src/lib/         Session handling, timezone helpers, shared utilities
deploy/
  nginx/           web container templates
  backup/          database backup script
  monitoring/      Prometheus config and alert rules
```

## Run the development stack

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

The overlay mounts the source into the containers, enables uvicorn `--reload`, starts the Vite dev server on port
5173 (which forwards `/api` to the backend, so cookies and WebSockets behave as in production), and publishes the
API (8000), PostgreSQL (5432) and Redis (6379) on the host. Use it only on a machine you trust.

- Dashboard: `http://localhost:5173`
- API docs (only when `DEBUG=true`): `http://localhost:8000/docs`

## Tests

Backend tests run inside the backend container, where the dependencies are installed:

```bash
docker exec -w /app patching-backend python -m pytest -q
```

Test tools are not in the production image. The development overlay builds the backend with them (`INSTALL_DEV=1`).
The production image has no `pip` at run time, so test tools cannot be added to a running production container. Run the
tests from the development overlay, which builds with them installed.

Dependencies are pinned in `backend/requirements.txt` and `frontend-web/package-lock.json`. The frontend installs with
`npm ci`, so it uses exactly the locked versions.

To run one file:

```bash
docker exec -w /app patching-backend python -m pytest -q tests/test_restore_preflight.py
```

The suite covers the cron logic, compliance mapping, profiles and discovery, TLS and secret sealing, maintenance
windows, TOTP and password handling, the Proxmox restore preflight, and the off-host copy and security guards. The
Proxmox and SFTP calls are replaced with fakes; nothing in the suite contacts a real server.

The frontend has no automated test runner yet. Verify it with a production build:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec frontend npm run build
```

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: backend tests with a Postgres service, `pip-audit`,
the frontend type check and production build, `npm audit`, Trivy scans of both container images (failing on high and
critical findings with a fix available), a CycloneDX SBOM, and a secret scan. `.github/dependabot.yml` opens weekly
update pull requests for Python, npm, base images and actions.

The workflow has not been run on GitHub yet. Run it once after the repository is pushed, and fix anything it reports.

## Database migrations

Schema changes are Alembic revisions in `backend/migrations/versions`. The backend applies pending revisions when it
starts, so a rebuilt container migrates the database.

```bash
# create a revision (edit the generated file: add the upgrade and downgrade steps)
docker exec -w /app patching-backend alembic revision -m "describe the change"

# apply pending revisions now
docker exec -w /app patching-backend alembic upgrade head

# show history
docker exec -w /app patching-backend alembic history
```

Write upgrades that keep existing rows. The audit trail backfill in `0004_audit_trail.py` is a good example.
Sealed columns use `app.models.types.SealedJSON`, so existing secrets stay encrypted.

## Adding a setting that holds a secret

1. Store it in a `SealedJSON` column (see `SystemSettings.offsite_config`).
2. Return only whether the secret is set, never its value, from the GET endpoint.
3. Keep the stored value when a blank field is submitted.
4. Record configuration changes with `audit.record("config.update", ...)` and `audit.diff`. Mark secrets as changed
   without storing them.

## Audit events

Mutating API requests, and every 401, 403 and 429, are recorded by `app/middleware/audit.py`. Code that performs
a notable action (for example a restore or an upload) records its own event with `app/services/audit.py`:

```python
from app.services import audit
await audit.record("offsite.upload", category="backup", source="worker", resource_id=name)
```

`record` never raises, so an audit failure cannot break the operation it describes. Use a dotted action name, a
category from `audit.category_for`, and put only non-secret detail in `details`.

## Frontend conventions

- Every `/api/v1` request goes through `window.fetch`, which `lib/auth.ts` wraps to add the access token and renew
  the session once on a 401.
- Times are shown through `lib/displayTime.ts`, so the display timezone setting applies everywhere.
- Long-running work returns a task id. Poll `GET /api/v1/backup/tasks/{id}` (or the job endpoints) rather than
  holding a request open.
- Live job progress comes from the WebSocket `/api/v1/jobs/ws/{job_id}/monitor`. It takes the access token as
  `?token=` because browsers cannot set headers on a WebSocket.

## Where job logic lives

- `app/tasks.py` (`async_execute_job`, `execute_on_server`, `execute_patches`) is the patch runner: waves, health
  checks, rollback, reboot handling, Kubernetes ordering and the final job status. `execute_on_server` holds the
  server's lock (`app/services/server_locks.py`) for the whole run.
- `app/services/job_service.py` creates and cancels jobs and builds status and failed-server lists for the API. Patch
  execution does not live there.
- `app/services/preflight_service.py` implements the pre-flight checks behind `POST /jobs/{id}/preflight-check`.

## Troubleshooting

- Backend changes are not picked up: in production the image must be rebuilt (`docker compose up -d --build backend
  worker beat`). The dev overlay reloads automatically.
- Worker tasks do not run: check `docker compose logs worker` and that Redis is healthy.
- A migration fails at start: `docker compose logs backend` shows the Alembic error. Fix the revision and restart;
  do not edit `alembic_version` by hand.
