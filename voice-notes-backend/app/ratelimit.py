# app/ratelimit.py — in-process sliding-window rate limits.
#
# State lives in this process, so it is per-instance: fine for one uvicorn
# worker (the current deployment). Move the counters to Redis before running
# several instances, or each one will allow the full limit on its own.
import math, time
from collections import deque
from fastapi import Depends, HTTPException, Request, status
from app.config import settings
from app.deps import get_current_user
from app.schemas import TranscriptPayload


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def retry_after(self, key: str) -> float | None:
        """Record a hit for `key`. Returns None if allowed, else seconds until the next slot frees up."""
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


# One limiter per protected surface, shared across requests.
auth_limiter = RateLimiter(settings.RATE_AUTH_PER_MIN, 60)
llm_limiter = RateLimiter(settings.RATE_LLM_PER_MIN, 60)
stream_limiter = RateLimiter(settings.RATE_STREAMS_PER_MIN, 60)


async def limit_auth_by_ip(request: Request):
    """Dependency for login/signup: slows credential stuffing."""
    wait = auth_limiter.retry_after(client_ip(request))
    if wait is not None:
        raise _too_many(wait)


async def limit_llm_by_user(payload: TranscriptPayload, user: dict = Depends(get_current_user)):
    """Dependency for anything that calls Groq on the user's behalf.

    Declares the request body so FastAPI validates it before this runs: a
    malformed request gets its 422 without consuming a slot."""
    wait = llm_limiter.retry_after(user["id"])
    if wait is not None:
        raise _too_many(wait)
