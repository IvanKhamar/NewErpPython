"""The login throttle with its counters in Redis, so every worker and container counts the same failures.

The rules are LoginThrottle's: a window starts with the first failure and the limits are per username and per
address. Redis keeps a counter per key with the window as its time to live (INCR, and EXPIRE only on the first
failure), so the window ends by itself."""

from datetime import timedelta

from redis.asyncio import Redis

from newerp.application.auth.throttle import LoginThrottleOptions

PREFIX = "newerp:login:"


class RedisLoginThrottle:
    def __init__(self, redis: Redis, options: LoginThrottleOptions) -> None:
        self._redis = redis
        self._options = options

    async def retry_after(self, address: str | None, username: str | None) -> timedelta | None:
        wait: timedelta | None = None
        for key, limit in self._keys(address, username):
            count = await self._redis.get(key)
            if count is not None and int(count) >= limit:
                left = await self._redis.ttl(key)  # seconds; -2: the key just expired
                if left > 0:
                    wait = max(wait or timedelta(0), timedelta(seconds=left))
        return wait

    async def failed(self, address: str | None, username: str | None) -> None:
        window = self._options.window_minutes * 60
        for key, _ in self._keys(address, username):
            if await self._redis.incr(key) == 1:
                await self._redis.expire(key, window)

    async def succeeded(self, username: str | None) -> None:
        await self._redis.delete(self._user_key(username))

    def _keys(self, address: str | None, username: str | None) -> list[tuple[str, int]]:
        return [
            (self._user_key(username), self._options.max_failures_per_user),
            (PREFIX + "ip:" + (address or "unknown"), self._options.max_failures_per_address),
        ]

    @staticmethod
    def _user_key(username: str | None) -> str:
        return PREFIX + "user:" + (username or "").strip().lower()
