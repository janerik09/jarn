from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
from app.config import settings
from app.database import init_db, close_db
from app.security import require_credential_key, require_secret_key
from app.services.secret_migration import seal_legacy_secrets
from app.services.tls import apply_from_database as apply_tls_files
from app.logging_config import configure_logging, get_logger

# Import all models to register them with Base BEFORE init_db is called
import app.models  # noqa: F401

from app.api.v1 import auth, servers, patches, jobs, schedules, audit, admin, demo, patch_groups, distro_upgrades, compliance, backups, tls as tls_api, users, offsite as offsite_api, host_keys as host_keys_api, platform as platform_api
from app.api.deps import guard, READ, ADMIN
from app.api import health

# Configure logging
configure_logging()
logger = get_logger(__name__)

# Initialize Celery
from app.celery_app import app as celery_app  # noqa: F401, E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("app_startup", app_name=settings.app_name, version=settings.app_version)
    require_credential_key()
    require_secret_key()
    await init_db()
    await seal_legacy_secrets()
    await apply_tls_files()
    from app.services import audit as _audit
    from app.config import settings as _settings
    await _audit.record("system.start", category="system", source="system", details={"version": _settings.app_version, "environment": _settings.environment})
    yield
    # Shutdown
    await _audit.record("system.stop", category="system", source="system")
    logger.info("app_shutdown")
    await close_db()


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Enterprise-grade patch management tool",
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

# Audit trail for every change made through the API
from app.middleware.audit import AuditMiddleware  # noqa: E402
app.add_middleware(AuditMiddleware)

# CORS is off unless CORS_ORIGINS lists the origins that may call the API (the bundled dashboard is same-origin)
_cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )


# Health check endpoint
@app.get("/health")
async def health_check():
    from sqlalchemy import text
    from app.database import engine
    checks = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {type(e).__name__}"
    try:
        import redis.asyncio as redis_async
        client = redis_async.from_url(settings.redis_url, socket_connect_timeout=3)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"
    try:
        from app.services.monitoring import worker_count
        workers = await worker_count(timeout=2.0)
        checks["worker"] = "ok" if workers else "no worker responding"
    except Exception as e:
        checks["worker"] = f"error: {type(e).__name__}"
    healthy = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if healthy else "degraded", "app": settings.app_name, "checks": checks},
    )


# API v1 routes
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(servers.router, prefix="/api/v1/servers", tags=["servers"], dependencies=[Depends(guard())])
app.include_router(patches.router, prefix="/api/v1/patches", tags=["patches"], dependencies=[Depends(guard())])
app.include_router(patch_groups.router, prefix="/api/v1/patch-groups", tags=["patch-groups"], dependencies=[Depends(guard())])
app.include_router(jobs.router, prefix="/api/v1/jobs", tags=["jobs"], dependencies=[Depends(guard())])
app.include_router(schedules.router, prefix="/api/v1/schedules", tags=["schedules"], dependencies=[Depends(guard())])
app.include_router(distro_upgrades.router, prefix="/api/v1/servers", tags=["distro-upgrades"], dependencies=[Depends(guard())])
app.include_router(distro_upgrades.candidates_router, prefix="/api/v1/distro-upgrades", tags=["distro-upgrades"], dependencies=[Depends(guard())])
app.include_router(audit.router, prefix="/api/v1/audit-logs", tags=["audit"], dependencies=[Depends(guard())])
app.include_router(compliance.router, prefix="/api/v1/compliance", tags=["compliance"], dependencies=[Depends(guard())])
app.include_router(backups.router, prefix="/api/v1", tags=["backups"], dependencies=[Depends(guard())])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(guard(read=READ, write=ADMIN))])
app.include_router(tls_api.router, prefix="/api/v1/admin/tls", tags=["tls"], dependencies=[Depends(guard(read=READ, write=ADMIN))])
app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
app.include_router(platform_api.router, prefix="/api/v1/platform", tags=["platform"], dependencies=[Depends(guard())])
app.include_router(host_keys_api.router, prefix="/api/v1/admin/host-keys", tags=["host-keys"],
                   dependencies=[Depends(guard(read=ADMIN, write=ADMIN))])
app.include_router(offsite_api.router, prefix="/api/v1/admin/offsite", tags=["offsite"],
                   dependencies=[Depends(guard(read=ADMIN, write=ADMIN))])
from app.api import metrics as metrics_api  # noqa: E402
app.include_router(metrics_api.router, tags=["monitoring"])
if settings.debug:  # Demo data endpoints are never mounted in production
    app.include_router(demo.router, prefix="/api/v1", tags=["demo"], dependencies=[Depends(guard())])

# Root endpoint
@app.get("/")
async def root():
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs" if settings.debug else "N/A",
    }


# Error handlers
@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error("unhandled_exception", path=request.url.path, error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
