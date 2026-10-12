"""
Notifications by webhook (Slack, Teams, or any endpoint that accepts JSON with a "text" field) and by email.
Messages carry summaries only, never credentials. A failed delivery is logged and never fails the work that
triggered it. Settings live in SystemSettings.notification_config, which is encrypted at rest.
"""

from email.message import EmailMessage
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.logging_config import get_logger
from app.models.settings import SystemSettings

logger = get_logger(__name__)


async def config() -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return dict(row.notification_config or {}) if row else {}


async def webhook_url() -> Optional[str]:
    return (await config()).get("webhook_url") or None


def _email_settings(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    smtp = cfg.get("smtp") or {}
    if not smtp.get("host") or not smtp.get("from") or not smtp.get("to"):
        return None
    return smtp


async def send_email(smtp: Dict[str, Any], subject: str, body: str) -> bool:
    import aiosmtplib
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp["from"]
    message["To"] = ", ".join(t.strip() for t in smtp["to"].split(",") if t.strip())
    message.set_content(body)
    try:
        await aiosmtplib.send(
            message,
            hostname=smtp["host"],
            port=int(smtp.get("port") or 587),
            username=smtp.get("username") or None,
            password=smtp.get("password") or None,
            start_tls=bool(smtp.get("starttls", True)),
            timeout=15,
        )
        return True
    except Exception as e:
        logger.warning("notification_email_failed", error=str(e))
        return False


async def send(event: str, title: str, text: str = "") -> bool:
    cfg = await config()
    delivered = False
    url = cfg.get("webhook_url")
    if url:
        message = f"*{title}*" + (f"\n{text}" if text else "")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json={"text": message, "event": event, "title": title})
            if response.status_code < 300:
                delivered = True
            else:
                logger.warning("notification_rejected", event=event, status=response.status_code)
        except Exception as e:
            logger.warning("notification_failed", event=event, error=str(e))
    smtp = _email_settings(cfg)
    if smtp:
        delivered = await send_email(smtp, title, text or title) or delivered
    return delivered
