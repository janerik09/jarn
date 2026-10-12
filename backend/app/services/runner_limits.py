"""
Small helpers the job runner uses: a concurrency limit for servers patched at once, and retries for SSH connects.
Kept free of database and SSH code so they can be tested directly.
"""

import asyncio
from typing import Awaitable, Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")

CONNECT_ATTEMPTS = 3
CONNECT_RETRY_DELAYS = (10, 30)  # seconds to wait after the first and second failed attempts
DEFAULT_MAX_CONCURRENT_SERVERS = 5


def concurrency_limit(value) -> int:
    """The configured servers-at-once value, never below 1. Missing or invalid values use the default."""
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT_SERVERS


async def run_limited(items: Sequence[T], limit: int, worker: Callable[[T], Awaitable[None]]) -> None:
    """Runs worker for every item, with at most `limit` running at once. Waits for all of them."""
    semaphore = asyncio.Semaphore(concurrency_limit(limit))

    async def guarded(item: T) -> None:
        async with semaphore:
            await worker(item)

    await asyncio.gather(*(guarded(item) for item in items))


async def connect_with_retry(connect: Callable[[], Awaitable[bool]],
                             sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> bool:
    """Tries to open an SSH connection a few times. Only used before anything on the server has changed."""
    for attempt in range(CONNECT_ATTEMPTS):
        if await connect():
            return True
        if attempt < CONNECT_ATTEMPTS - 1:
            await sleep(CONNECT_RETRY_DELAYS[attempt])
    return False
