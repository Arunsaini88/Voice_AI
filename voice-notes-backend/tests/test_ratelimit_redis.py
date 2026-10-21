# tests/test_ratelimit_redis.py — the Redis backend against a real server.
#
# Skipped unless REDIS_TEST_URL is set (CI starts a redis service; locally:
#   docker run -d --rm -p 6379:6379 redis:7-alpine
#   REDIS_TEST_URL=redis://localhost:6379/15 python -m pytest tests/test_ratelimit_redis.py
# Uses its own DB number and flushes it, so point it at a scratch database.
import asyncio, os
import pytest
import pytest_asyncio
from app.config import settings
from app.ratelimit import RedisRateLimiter, RedisStreamSlots

URL = os.environ.get("REDIS_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="REDIS_TEST_URL not set")


@pytest_asyncio.fixture
async def redis_client():
    import redis.asyncio as redis
    client = redis.from_url(URL, socket_timeout=2, socket_connect_timeout=2)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_sliding_window_is_shared_and_expires(redis_client):
    # Two limiter objects with the same name = two workers sharing one window.
    a = RedisRateLimiter(redis_client, "t", limit=2, window_seconds=1)
    b = RedisRateLimiter(redis_client, "t", limit=2, window_seconds=1)
    assert await a.hit("ip1") is None
    assert await b.hit("ip1") is None
    wait = await a.hit("ip1")
    assert wait is not None and 0 < wait <= 1.0
    assert await b.hit("ip2") is None                   # keys independent
    await asyncio.sleep(1.1)
    assert await a.hit("ip1") is None                   # window slid


async def test_limiter_names_are_isolated(redis_client):
    a = RedisRateLimiter(redis_client, "auth", limit=1, window_seconds=60)
    b = RedisRateLimiter(redis_client, "llm", limit=1, window_seconds=60)
    assert await a.hit("k") is None
    assert await b.hit("k") is None
    assert await a.hit("k") is not None


async def test_keys_carry_a_ttl(redis_client):
    lim = RedisRateLimiter(redis_client, "ttl", limit=5, window_seconds=30)
    await lim.hit("k")
    ttl = await redis_client.pttl("rl:ttl:k")
    assert 0 < ttl <= 30_000


async def test_stream_slots_shared_across_workers(redis_client):
    a = RedisStreamSlots(redis_client, ttl_seconds=60)
    b = RedisStreamSlots(redis_client, ttl_seconds=60)
    assert await a.acquire("u", 2)
    assert await b.acquire("u", 2)
    assert not await a.acquire("u", 2)                  # third worker sees the cap
    await b.release("u")
    assert await a.acquire("u", 2)
    await a.release("u"); await a.release("u")
    assert await redis_client.exists("slots:u") == 0    # key removed at zero
    await a.release("u")                                # over-release: no negative counter left behind
    assert await redis_client.exists("slots:u") == 0


async def test_slot_key_has_safety_ttl(redis_client):
    slots = RedisStreamSlots(redis_client, ttl_seconds=45)
    await slots.acquire("u", 1)
    ttl = await redis_client.pttl("slots:u")
    assert 0 < ttl <= 45_000


async def test_unreachable_redis_fails_open_or_closed(monkeypatch):
    import redis.asyncio as redis
    dead = redis.from_url("redis://127.0.0.1:1/0", socket_timeout=0.3, socket_connect_timeout=0.3)
    lim = RedisRateLimiter(dead, "x", limit=1, window_seconds=60)
    slots = RedisStreamSlots(dead, ttl_seconds=10)

    monkeypatch.setattr(settings, "RATE_LIMIT_FAIL_OPEN", True)
    assert await lim.hit("k") is None
    assert await slots.acquire("k", 1) is True

    monkeypatch.setattr(settings, "RATE_LIMIT_FAIL_OPEN", False)
    assert await lim.hit("k") is not None
    assert await slots.acquire("k", 1) is False
    await dead.aclose()
