"""Encrypt credential rows that were stored in clear text before SealedJSON existed.

Safe to run on every startup: rows that are already sealed are skipped.
"""

from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified

from app.database import AsyncSessionLocal
from app.models.server import Server
from app.models.settings import SystemSettings
from app.logging_config import get_logger

logger = get_logger(__name__)


async def seal_legacy_secrets() -> None:
    async with AsyncSessionLocal() as db:
        server_ids = (await db.execute(text(
            "SELECT id FROM servers WHERE NOT (credentials ? '__sealed')"
        ))).scalars().all()
        for server_id in server_ids:
            server = await db.get(Server, server_id)
            # The column type decrypts on load and encrypts on flush. flag_modified forces the UPDATE,
            # because re-assigning an equal dict is not seen as a change.
            flag_modified(server, "credentials")

        settings_ids = (await db.execute(text(
            "SELECT id FROM system_settings WHERE NOT (backup_config ? '__sealed') OR NOT (tls_config ? '__sealed')"
        ))).scalars().all()
        for settings_id in settings_ids:
            row = await db.get(SystemSettings, settings_id)
            flag_modified(row, "backup_config")
            flag_modified(row, "tls_config")

        await db.commit()

    if server_ids or settings_ids:
        logger.info("secrets_sealed", servers=len(server_ids), settings_rows=len(settings_ids))
