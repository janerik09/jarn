"""
One patch run at a time per server. Two jobs, a double-clicked job or a scheduled run cannot both change the same
machine: whichever starts second is refused, and the refusal is recorded against its server.

The lock is a Redis key that expires after LOCK_SECONDS, so a crashed worker cannot hold a server forever. It is
released only by the run that took it.
"""

import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator

import redis.asyncio as redis_async

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)
LOCK_SECONDS = 4 * 3600  # longer than the longest patch run; the watchdog fails runs that go past 200 minutes
_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class ServerBusy(Exception):
    def __init__(self, holder: str):
        super().__init__(f"The server is busy with another operation ({holder}). Nothing was changed.")
        self.holder = holder


def _key(server_id: str) -> str:
    return f"jarn:server-lock:{server_id}"


@asynccontextmanager
async def server_lock(server_id: str, label: str) -> AsyncIterator[None]:
    client = redis_async.from_url(settings.redis_url, socket_connect_timeout=3)
    token = f"{label}:{uuid.uuid4().hex}"
    try:
        acquired = await client.set(_key(server_id), token, nx=True, ex=LOCK_SECONDS)
        if not acquired:
            holder = await client.get(_key(server_id))
            raise ServerBusy(holder.decode().split(":")[0] if holder else "unknown")
        logger.info("server_lock_acquired", server_id=server_id, label=label)
        try:
            yield
        finally:
            await client.eval(_RELEASE, 1, _key(server_id), token)
            logger.info("server_lock_released", server_id=server_id, label=label)
    finally:
        await client.aclose()
