import logging
import structlog
from app.config import settings

def configure_logging():
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
        wrapper_class=structlog.stdlib.BoundLogger,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=None,
        level=getattr(logging, settings.log_level),
    )


def get_logger(name: str):
    return structlog.get_logger(name)


class AuditLogger:
    """Specialized logger for audit trails - immutable append-only logs"""

    def __init__(self):
        self.logger = get_logger("audit")

    def log_action(self, actor_id: str, action: str, resource_type: str,
                   resource_id: str, status: str = "success",
                   changes: dict = None, reason: str = None, **kwargs):
        self.logger.info(
            "audit_action",
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            changes=changes or {},
            reason=reason,
            **kwargs
        )
