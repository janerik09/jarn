import os
from celery import Celery
from celery.schedules import crontab

redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

app = Celery(__name__)
app.conf.update(
    broker_url=redis_url,
    result_backend=redis_url,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=180 * 60,  # 180 minutes hard limit
    task_soft_time_limit=175 * 60,  # 175 minutes soft limit
    worker_prefetch_multiplier=1,  # Process one task at a time
    worker_max_tasks_per_child=1000,
    beat_schedule={
        "rescan-stale-servers": {"task": "app.tasks.rescan_stale_servers", "schedule": 3600.0},
        "check-certificate-expiry": {"task": "app.tasks.check_certificate_expiry", "schedule": 86400.0},
        "run-due-schedules": {"task": "app.tasks.run_due_schedules", "schedule": 60.0},
        "fail-stuck-jobs": {"task": "app.tasks.fail_stuck_jobs", "schedule": 300.0},
        "evaluate-alerts": {"task": "app.tasks.evaluate_alerts", "schedule": 300.0},
        "scheduled-compliance": {"task": "app.tasks.scheduled_compliance", "schedule": 86400.0},
        "archive-audit": {"task": "app.tasks.archive_audit", "schedule": 86400.0},
        # Does nothing unless the off-host copy is turned on in Admin Settings
        "offsite-backup": {"task": "app.tasks.run_offsite_backup", "schedule": crontab(hour=3, minute=30)},
    },
)

app.autodiscover_tasks(["app.tasks"])


from celery.signals import worker_ready, worker_shutdown  # noqa: E402


@worker_ready.connect
def _record_worker_start(**kwargs):
    import asyncio as _asyncio
    from app.services import audit as _audit
    _asyncio.run(_audit.record("system.worker.start", category="system", source="worker", details={"role": "celery worker"}))


@worker_shutdown.connect
def _record_worker_stop(**kwargs):
    import asyncio as _asyncio
    from app.services import audit as _audit
    _asyncio.run(_audit.record("system.worker.stop", category="system", source="worker", details={"role": "celery worker"}))
