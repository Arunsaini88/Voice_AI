# tests/test_ratelimit.py
import pytest
from app import ratelimit
from app.ratelimit import RateLimiter

NOTE = {"transcript": "Buy milk.", "language": "en"}


def test_sliding_window(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    lim = RateLimiter(limit=2, window_seconds=60)

    assert lim.retry_after("k") is None
    assert lim.retry_after("k") is None
    wait = lim.retry_after("k")
    assert wait == pytest.approx(60)            # blocked until the first hit ages out
    assert lim.retry_after("other") is None     # keys are independent

    now[0] += 30
    assert lim.retry_after("k") == pytest.approx(30)
    now[0] += 31
    assert lim.retry_after("k") is None         # first hit expired, slot freed


def test_prune_keeps_memory_bounded(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    lim = RateLimiter(limit=1, window_seconds=10)
    for i in range(10_001):
        lim.retry_after(f"ip-{i}")
    now[0] += 11
    lim.retry_after("fresh")                    # crosses the prune threshold with everything stale
    assert len(lim._hits) < 100


async def test_auth_limit_per_ip(client, monkeypatch):
    monkeypatch.setattr(ratelimit, "auth_limiter", RateLimiter(2, 60))
    creds = {"email": "dave@example.com", "password": "correct-horse-battery"}
    assert (await client.post("/auth/signup", json=creds)).status_code == 201
    assert (await client.post("/auth/login", json=creds)).status_code == 200
    r = await client.post("/auth/login", json=creds)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


async def test_llm_limit_shared_by_notes_and_summarize(client, user, monkeypatch):
    monkeypatch.setattr(ratelimit, "llm_limiter", RateLimiter(2, 60))
    assert (await client.post("/llm/summarize", json=NOTE, headers=user.headers)).status_code == 200
    assert (await client.post("/notes", json=NOTE, headers=user.headers)).status_code == 201
    assert (await client.post("/notes", json=NOTE, headers=user.headers)).status_code == 429


async def test_invalid_body_does_not_consume_llm_quota(client, user, monkeypatch):
    monkeypatch.setattr(ratelimit, "llm_limiter", RateLimiter(1, 60))
    for _ in range(3):
        r = await client.post("/notes", json={"transcript": "", "language": "en"}, headers=user.headers)
        assert r.status_code == 422
    assert (await client.post("/notes", json=NOTE, headers=user.headers)).status_code == 201


async def test_llm_limit_is_per_user(client, user, other_user, monkeypatch):
    monkeypatch.setattr(ratelimit, "llm_limiter", RateLimiter(1, 60))
    assert (await client.post("/notes", json=NOTE, headers=user.headers)).status_code == 201
    assert (await client.post("/notes", json=NOTE, headers=user.headers)).status_code == 429
    assert (await client.post("/notes", json=NOTE, headers=other_user.headers)).status_code == 201


def test_client_ip_respects_proxy_setting(monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    req = SimpleNamespace(headers={"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, client=SimpleNamespace(host="10.0.0.1"))
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    assert ratelimit.client_ip(req) == "10.0.0.1"
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    assert ratelimit.client_ip(req) == "203.0.113.9"
