"""The login throttle in Redis (used on a server): the same rules as the in-memory one, shared by all workers.
Needs Redis (docker compose, or the CI service); without it the test is skipped."""

import asyncio
import os
import uuid

import pytest
from redis.asyncio import Redis

from newerp.application.auth.throttle import LoginThrottleOptions
from newerp.infrastructure.auth.redis_throttle import RedisLoginThrottle

URL = os.environ.get("NEWERP_TEST_REDIS", "redis://localhost:6379/15")


def test_two_workers_share_the_counters_and_success_resets_the_user():
    async def run() -> None:
        redis = Redis.from_url(URL)
        try:
            await redis.ping()
        except OSError:
            pytest.skip("Redis is not running")
        options = LoginThrottleOptions(max_failures_per_user=2, max_failures_per_address=3, window_minutes=1)
        first, second = RedisLoginThrottle(redis, options), RedisLoginThrottle(redis, options)
        user, ip = f"user-{uuid.uuid4()}", f"ip-{uuid.uuid4()}"
        try:
            await first.failed(ip, user)
            assert await second.retry_after(ip, user) is None
            await second.failed(ip, user)  # the second failure, counted by another worker
            wait = await first.retry_after(ip, user)
            assert wait is not None and 0 < wait.total_seconds() <= 60
            await first.succeeded(user)
            assert await second.retry_after(ip, user) is None  # the address has 2 of 3
            await first.failed(ip, "someone-else")
            assert await second.retry_after(ip, "anyone") is not None  # the address is closed now
        finally:
            await redis.delete(f"newerp:login:user:{user}", "newerp:login:user:someone-else", f"newerp:login:ip:{ip}")
            await redis.aclose()

    asyncio.run(run())
