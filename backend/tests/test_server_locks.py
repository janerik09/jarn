import asyncio
import uuid

import pytest

from app.services.server_locks import ServerBusy, server_lock


def test_second_run_on_the_same_server_is_refused_until_the_first_finishes():
    server = f"test-{uuid.uuid4().hex}"

    async def scenario():
        async with server_lock(server, "job-A"):
            with pytest.raises(ServerBusy) as refused:
                async with server_lock(server, "job-B"):
                    pass
            assert "job-A" in refused.value.holder
        async with server_lock(server, "job-B"):
            pass

    asyncio.run(scenario())


def test_lock_on_one_server_does_not_block_another():
    a, b = f"test-{uuid.uuid4().hex}", f"test-{uuid.uuid4().hex}"

    async def scenario():
        async with server_lock(a, "job-A"):
            async with server_lock(b, "job-B"):
                pass

    asyncio.run(scenario())


def test_lock_is_released_when_the_run_fails():
    server = f"test-{uuid.uuid4().hex}"

    async def scenario():
        with pytest.raises(RuntimeError):
            async with server_lock(server, "job-A"):
                raise RuntimeError("install failed")
        async with server_lock(server, "job-B"):
            pass

    asyncio.run(scenario())
