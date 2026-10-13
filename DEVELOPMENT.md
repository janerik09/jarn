# Development

## Layout

```
backend/
  app/
    api/v1/        HTTP routes, one module per area (auth, sso, servers, jobs, backups, offsite, audit, ...)
    services/      The work itself: backup providers (Proxmox and file), off-host copy, audit, notifications, TLS, SSO, server locks
    models/        SQLAlchemy models
    tasks.py       Celery tasks: patch jobs, backups, restores, scheduled work
    celery_app.py  Celery configuration and the beat schedule
    security.py    Password hashing, tokens, and AES-256-GCM sealing of secrets
  migrations/      Alembic revisions, applied when the backend starts
  tests/           pytest suite
frontend-web/
  src/components/  The screens (AdminSettings, ServerBackups, AuditLog, and so on)
  src/lib/         Session handling, timezone helpers, shared utilities
deploy/
  nginx/           Templates for the web container
  backup/          Database backup script
  monitoring/      Prometheus config and alert rules
```

## Running the development stack

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
```

The overlay mounts the source into the containers and runs uvicorn with `--reload`. It starts the Vite dev server on
port 5173, which forwards `/api` to the backend, so cookies and WebSockets behave the same as in production. It also
publishes the API (8000), PostgreSQL (5432) and Redis (6379) on the host. Only use it on a machine you trust.

- Dashboard: `http://localhost:5173`
- API docs: `http://localhost:8000/docs`, but only when `DEBUG=true`

## Tests

The production image has no test tools and no `pip`, so you can't install them into a running production container.
The development overlay builds the backend with the test tools included (`INSTALL_DEV=1`), so run the tests there.

To build the development image and run the whole suite:

```bash
docker build --build-arg INSTALL_DEV=1 -t jarn-backend:dev backend
docker run --rm --network patching-network \
  -e REDIS_URL=redis://redis:6379/0 \
  -e DATABASE_URL=postgresql://patching_user:<password>@postgres:5432/patching_db \
  -e SECRET_KEY=<at least 32 characters> \
  -e CREDENTIAL_ENCRYPTION_KEY=<base64 of 32 bytes> \
  jarn-backend:dev sh -c "cd /app && python -m pytest -q"
```

The lock tests need Redis, and the rest of the suite is fine without it. To run one file, add its path after `pytest -q`.

The suite covers the cron logic, compliance mapping, profiles and discovery, TLS and secret sealing, maintenance
windows, TOTP and passwords, the Proxmox restore preflight, file backups (archive, checksum, restore and retention),
the off-host copy and security checks, SSO, the per-server locks, and an import test for the whole application. The
Proxmox, SSH and SFTP calls are replaced with fakes, so the suite never contacts a real server.

There's no automated test runner for the frontend yet. The check for now is a production build:

```bash
docker run --rm -v "$PWD/frontend-web:/app" -w /app node:22-alpine sh -c "npm ci && npm run build"
```

Dependencies are pinned. The backend uses exact versions in `backend/requirements.txt`, and the frontend uses
`frontend-web/package-lock.json`. The frontend installs with `npm ci`, which installs exactly what the lockfile says.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and every pull request. It has four jobs:

- **Backend:** installs the dependencies, runs `pip-audit`, and runs the tests against Postgres and Redis services.
- **Frontend:** runs `npm ci`, `npm audit` and the production build.
- **Images:** builds both container images and scans them with Trivy, failing on high and critical findings that have
  a fix. It also writes a CycloneDX SBOM.
- **Secrets:** runs the gitleaks command over the full git history.

The workflow's comments explain the choices that aren't obvious: why Trivy runs from a pinned container, why the secret
scan doesn't use the gitleaks action, and why it has read-only permissions. `.github/dependabot.yml` opens weekly
update pull requests and groups the minor and patch updates together.

## Database migrations

Schema changes are Alembic revisions in `backend/migrations/versions`. The backend applies pending revisions when it
starts, so rebuilding the container also migrates the database.

```bash
# create a revision, then edit the generated file to add the upgrade and downgrade steps
docker exec -w /app patching-backend alembic revision -m "describe the change"

# apply pending revisions now
docker exec -w /app patching-backend alembic upgrade head

# show the history
docker exec -w /app patching-backend alembic history
```

Write upgrades that keep existing rows. `0004_audit_trail.py` backfills the audit columns for the existing events, and
it's a good model to follow. Sealed columns use `app.models.types.SealedJSON`, so secrets stay encrypted.

## Adding a setting that holds a secret

1. Store it in a `SealedJSON` column, as `SystemSettings.offsite_config` does.
2. Let the GET endpoint return only whether the secret is set, never its value.
3. When a blank field is submitted, keep the stored value.
4. Record configuration changes with `audit.record("config.update", ...)` and `audit.diff`. Mark secrets as changed,
   but don't store them.

## Audit events

Every mutating API request, and every 401, 403 and 429 response, is recorded by `app/middleware/audit.py`. When code
performs a significant action, such as a restore or an upload, it records its own event through `app/services/audit.py`:

```python
from app.services import audit
await audit.record("offsite.upload", category="backup", source="worker", resource_id=name)
```

`record` never raises, so a failure in the audit code can't break the operation it describes. Use a dotted action
name, take the category from `audit.category_for`, and only put non-secret detail in `details`.

## Frontend conventions

- Every `/api/v1` request goes through `window.fetch`. The wrapper in `lib/auth.ts` adds the access token, and renews
  the session once when it gets a 401.
- Times go through `lib/displayTime.ts`, so the display timezone setting applies everywhere.
- Long-running work returns a task id. Poll `GET /api/v1/backup/tasks/{id}`, or the job endpoints, instead of holding
  a request open.
- Live job progress comes from the WebSocket at `/api/v1/jobs/ws/{job_id}/monitor`. Browsers can't set headers on a
  WebSocket, so the access token goes in `?token=`.

## Where job logic lives

- `app/tasks.py` is the patch runner. `async_execute_job` starts a job, `execute_on_server` runs one server while
  holding that server's lock (`app/services/server_locks.py`), and `execute_patches` does the work itself: health
  checks, rollback, reboots, Kubernetes ordering and the final status.
- `app/services/job_service.py` creates and cancels jobs, and builds the status and failed-server lists the API uses.
  It doesn't run patches.
- `app/services/preflight_service.py` holds the pre-flight checks behind `POST /jobs/{id}/preflight-check`.

## Troubleshooting

- **Backend changes aren't showing up.** In production, rebuild the image with
  `docker compose up -d --build backend worker beat` for backend changes, and `docker compose up -d --build web` for
  frontend changes. The development overlay reloads on its own.
- **Worker tasks don't run.** Check `docker compose logs worker`, and check that Redis is healthy.
- **A migration fails at startup.** `docker compose logs backend` shows the Alembic error. Fix the revision and
  restart. Don't edit `alembic_version` by hand.
