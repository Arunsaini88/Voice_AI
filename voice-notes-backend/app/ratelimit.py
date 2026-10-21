# app/ratelimit.py — sliding-window rate limits and per-user stream slots.
#
# Two interchangeable backends, chosen once at import from REDIS_URL:
#
#   Memory  counters live in this process. Correct for exactly one uvicorn
#           worker. With N workers each one allows the full limit.
#   Redis   counters live in Redis, so every worker and instance shares them.
#           Each check is one atomic Lua call; time comes from the Redis
#           server so instances with drifting clocks still agree.
#
# Both expose the same async interface:
#   await limiter.hit(key)        -> None if allowed, else seconds to wait
#   await slots.acquire(key, max) -> True if a slot was taken
#   await slots.release(key)
import logging, math, time, uuid
from collections import deque
from fastapi import Depends, HTTPException, Request, status
from app.config import settings
from app.deps import get_current_user
from app.schemas import TranscriptPayload

log = logging.getLogger("app.ratelimit")


# ------------------------------------------------------------------ memory

class MemoryRateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    async def hit(self, key: str) -> float | None:
        now = time.monotonic()
        q = self._hits.get(key)
        if q is None:
            q = self._hits[key] = deque()
        while q and q[0] <= now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return q[0] + self.window - now
        q.append(now)
        if len(self._hits) > 10_000:
            self._prune(now)
        return None

    def _prune(self, now: float):
        for k in [k for k, q in self._hits.items() if not q or q[-1] <= now - self.window]:
            del self._hits[k]

    def reset(self):
        self._hits.clear()


class MemoryStreamSlots:
    def __init__(self):
        self._counts: dict[str, int] = {}

    async def acquire(self, key: str, max_slots: int) -> bool:
        if self._counts.get(key, 0) >= max_slots:
            return False
        self._counts[key] = self._counts.get(key, 0) + 1
        return True

    async def release(self, key: str) -> None:
        n = self._counts.get(key, 0) - 1
        if n <= 0:
            self._counts.pop(key, None)
        else:
            self._counts[key] = n

    def reset(self):
        self._counts.clear()


# ------------------------------------------------------------------- redis

# Sliding window over a sorted set: score = server time in ms, member = a
# unique id. Returns -1 when the hit is accepted, else ms until a slot frees.
_LUA_HIT = """
local key    = KEYS[1]
local window = tonumber(ARGV[1])
local limit  = tonumber(ARGV[2])
local member = ARGV[3]
local t      = redis.call('TIME')
local now    = t[1] * 1000 + math.floor(t[2] / 1000)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window)
  return -1
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
return tonumber(oldest[2]) + window - now
"""

# Counter with a safety TTL so a crashed worker can't pin a slot forever.
_LUA_ACQUIRE = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[2]) end
if n > tonumber(ARGV[1]) then
  redis.call('DECR', KEYS[1])
  return 0
end
return 1
"""

_LUA_RELEASE = """
local n = redis.call('DECR', KEYS[1])
if n <= 0 then redis.call('DEL', KEYS[1]) end
return n
"""


class RedisRateLimiter:
    def __init__(self, client, name: str, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._prefix = f"rl:{name}:"
        self._hit = client.register_script(_LUA_HIT)

    async def hit(self, key: str) -> float | None:
        try:
            r = await self._hit(keys=[self._prefix + key],
                                args=[int(self.window * 1000), self.limit, uuid.uuid4().hex])
        except Exception:  # noqa: BLE001 — connection errors, timeouts, auth
            log.exception("rate limiter: Redis unavailable (fail_open=%s)", settings.RATE_LIMIT_FAIL_OPEN)
            return None if settings.RATE_LIMIT_FAIL_OPEN else 5.0
        return None if r < 0 else r / 1000

    def reset(self):
        pass  # tests flush Redis themselves


class RedisStreamSlots:
    def __init__(self, client, ttl_seconds: int):
        self._acquire = client.register_script(_LUA_ACQUIRE)
        self._release = client.register_script(_LUA_RELEASE)
        self._ttl_ms = ttl_seconds * 1000

    async def acquire(self, key: str, max_slots: int) -> bool:
        try:
            return bool(await self._acquire(keys=[f"slots:{key}"], args=[max_slots, self._ttl_ms]))
        except Exception:  # noqa: BLE001
            log.exception("stream slots: Redis unavailable (fail_open=%s)", settings.RATE_LIMIT_FAIL_OPEN)
            return settings.RATE_LIMIT_FAIL_OPEN

    async def release(self, key: str) -> None:
        try:
            await self._release(keys=[f"slots:{key}"])
        except Exception:  # noqa: BLE001
            log.exception("stream slots: release failed")

    def reset(self):
        pass


# ----------------------------------------------------------------- wiring

_redis = None
if settings.REDIS_URL:
    import redis.asyncio as _redis_lib
    _redis = _redis_lib.from_url(settings.REDIS_URL, socket_timeout=2, socket_connect_timeout=2)
    log.info("rate limits backed by Redis")


def _limiter(name: str, limit: int, window: float):
    return RedisRateLimiter(_redis, name, limit, window) if _redis else MemoryRateLimiter(limit, window)


auth_limiter = _limiter("auth", settings.RATE_AUTH_PER_MIN, 60)
reset_limiter = _limiter("reset", settings.RATE_RESET_PER_MIN, 60)   # keyed by IP and by email address
llm_limiter = _limiter("llm", settings.RATE_LLM_PER_MIN, 60)
stream_limiter = _limiter("stream", settings.RATE_STREAMS_PER_MIN, 60)
stream_slots = (
    RedisStreamSlots(_redis, settings.MAX_DICTATION_SECONDS + 120) if _redis else MemoryStreamSlots()
)


async def close() -> None:
    if _redis is not None:
        await _redis.aclose()


# ------------------------------------------------------------ dependencies

def _too_many(retry_after: float) -> HTTPException:
    secs = max(1, math.ceil(retry_after))
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        f"Too many requests. Try again in {secs}s.",
        headers={"Retry-After": str(secs)},
    )


def client_ip(request: Request) -> str:
    if settings.TRUST_PROXY_HEADERS:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limit_auth_by_ip(request: Request):
    """Dependency for login/signup: slows credential stuffing."""
    wait = await auth_limiter.hit(client_ip(request))
    if wait is not None:
        raise _too_many(wait)


async def limit_reset_by_ip(request: Request):
    """Dependency for forgot-password: caps outbound mail per client."""
    wait = await reset_limiter.hit("ip:" + client_ip(request))
    if wait is not None:
        raise _too_many(wait)


async def reset_allowed_for_email(email: str) -> bool:
    """Per-address cap on reset mail. The route answers 202 either way so the
    limit itself can't be used to probe which addresses exist."""
    return await reset_limiter.hit("email:" + email) is None


async def limit_llm_by_user(payload: TranscriptPayload, user: dict = Depends(get_current_user)):
    """Dependency for anything that calls Groq on the user's behalf.

    Declares the request body so FastAPI validates it before this runs: a
    malformed request gets its 422 without consuming a slot."""
    wait = await llm_limiter.hit(user["id"])
    if wait is not None:
        raise _too_many(wait)
