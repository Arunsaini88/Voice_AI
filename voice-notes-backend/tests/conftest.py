# tests/conftest.py
#
# Environment is set *before* `app` is imported: Settings reads it at import
# time. Env vars beat the .env file, so the real keys and Neon URL are never
# touched. The DB is a throwaway SQLite file; Groq and Deepgram are faked in
# the fixtures below.
import os, pathlib, tempfile
from types import SimpleNamespace

_TMP = pathlib.Path(tempfile.mkdtemp(prefix="vn-test-"))
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
    "DEEPGRAM_API_KEY": "test-deepgram-key",
    "GROQ_API_KEY": "test-groq-key",
    "JWT_SECRET": "test-secret-not-for-production",
    "RATE_AUTH_PER_MIN": "1000",
    "RATE_LLM_PER_MIN": "1000",
    "RATE_STREAMS_PER_MIN": "1000",
    "LOG_LEVEL": "WARNING",
})

import httpx                      # noqa: E402
import pytest                     # noqa: E402
import pytest_asyncio             # noqa: E402
from app import ratelimit         # noqa: E402
from app.db import engine         # noqa: E402
from app.main import app          # noqa: E402
from app.models import Base       # noqa: E402
from app.routes import dictate    # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _fresh_state():
    """Empty tables and zeroed rate limiters for every test."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    for lim in (ratelimit.auth_limiter, ratelimit.llm_limiter, ratelimit.stream_limiter):
        lim._hits.clear()
    dictate._active_streams.clear()
    yield


FAKE_SUMMARY = {"title": "Dentist reminder", "summary": "Call the dentist tomorrow.", "key_points": ["call dentist"]}


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    """Routes never reach Groq. Tests that need a failing LLM override this."""
    async def fake(transcript, language="en"):
        return dict(FAKE_SUMMARY)
    monkeypatch.setattr("app.routes.notes.summarize_transcript", fake)
    monkeypatch.setattr("app.routes.summarizer.summarize_transcript", fake)


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _signup(client, email):
    r = await client.post("/auth/signup", json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 201, r.text
    body = r.json()
    return SimpleNamespace(id=body["user_id"], token=body["access_token"], refresh=body["refresh_token"],
                           headers={"Authorization": f"Bearer {body['access_token']}"}, email=email)


@pytest_asyncio.fixture
async def user(client):
    return await _signup(client, "alice@example.com")


@pytest_asyncio.fixture
async def other_user(client):
    return await _signup(client, "bob@example.com")


# ---------------------------------------------------------------- Deepgram fake

class FakeResult:
    def __init__(self, text, final):
        self.is_final = final
        self.channel = SimpleNamespace(alternatives=[SimpleNamespace(transcript=text)])


class FakeDeepgramConnection:
    """Stands in for deepgram.listen.websocket.v("1").

    Every audio chunk produces one partial; finish() produces one final.
    `start_ok` / `send_ok` flip the failure paths the route must handle."""
    def __init__(self):
        self.handlers = {}
        self.chunks = []
        self.options = None
        self.started = False
        self.finished = False
        self.start_ok = True
        self.send_ok = True

    def on(self, event, cb):
        self.handlers[event] = cb

    def _emit(self, event, *args):
        cb = self.handlers.get(event)
        if cb:
            cb(self, *args)

    def start(self, options):
        self.started = True
        self.options = options
        return self.start_ok

    def send(self, data):
        self.chunks.append(data)
        from deepgram import LiveTranscriptionEvents
        self._emit(LiveTranscriptionEvents.Transcript, FakeResult(f"partial {len(self.chunks)}", False))
        return self.send_ok

    def finish(self):
        self.finished = True
        from deepgram import LiveTranscriptionEvents
        self._emit(LiveTranscriptionEvents.Transcript, FakeResult("Hello world.", True))


@pytest.fixture
def fake_deepgram(monkeypatch):
    conn = FakeDeepgramConnection()

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.listen = SimpleNamespace(websocket=SimpleNamespace(v=lambda version: conn))

    monkeypatch.setattr("app.routes.dictate.DeepgramClient", FakeClient)
    return conn
