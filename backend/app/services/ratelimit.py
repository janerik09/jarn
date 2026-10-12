"""
Fixed-window request limits kept in Redis, so every backend container shares the same counts.
"""

import redis.asyncio as redis_async

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


async def allow(key: str, limit: int, window_seconds: int) -> bool:
    """Counts one hit against key. False once the limit is reached within the window.

    If Redis is unreachable this allows the request and logs it: the per-account sign-in lockout still applies.
    """
    client = redis_async.from_url(settings.redis_url, socket_connect_timeout=2)
    try:
        count = await client.incr(f"ratelimit:{key}")
        if count == 1:
            await client.expire(f"ratelimit:{key}", window_seconds)
        return count <= limit
    except Exception as e:
        logger.warning("ratelimit_unavailable", error=str(e))
        return True
    finally:
        await client.aclose()
