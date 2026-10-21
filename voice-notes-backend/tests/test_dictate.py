# tests/test_dictate.py — the WebSocket protocol, with Deepgram faked.
#
# Uses Starlette's sync TestClient: it runs the app on its own loop, which is
# what the route expects (Deepgram callbacks arrive from another thread and
# are marshalled back with run_coroutine_threadsafe).
import json, pathlib, wave
import pytest
from fastapi.testclient import TestClient
from app.config import settings
from app.deps import create_access_token
from app.main import app
from app.routes import dictate

WAV = pathlib.Path(__file__).resolve().parents[2] / "test_audio.wav"


@pytest.fixture
def ws_client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def token():
    return create_access_token("ws-user-1")


def _wav_chunks(n=3, frames=4000):
    with wave.open(str(WAV), "rb") as wf:
        return [wf.readframes(frames) for _ in range(n)]


def _drain(ws):
    """Collect messages until 'done' or the socket closes."""
    out = []
    while True:
        try:
            msg = ws.receive_json()
        except Exception:
            break
        out.append(msg)
        if msg["type"] == "done":
            break
    return out


def test_full_session_with_wav_audio(ws_client, token, fake_deepgram):
    chunks = _wav_chunks()
    with ws_client.websocket_connect(
        f"/dictate/stream?lang=en&encoding=linear16&sample_rate=16000&token={token}"
    ) as ws:
        for c in chunks:
            ws.send_bytes(c)
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = _drain(ws)

    assert fake_deepgram.started and fake_deepgram.finished
    assert fake_deepgram.chunks == chunks
    assert fake_deepgram.options.encoding == "linear16" and fake_deepgram.options.sample_rate == 16000
    assert fake_deepgram.options.model == "nova-3" and fake_deepgram.options.language == "en"
    types = [m["type"] for m in msgs]
    assert types.count("partial") == len(chunks)
    assert types[-2:] == ["final", "done"]           # trailing final flushed before done
    assert msgs[-2]["text"] == "Hello world."


def test_hinglish_uses_multi_language_and_romanizes_finals(ws_client, token, fake_deepgram, monkeypatch):
    async def fake_romanize(text, lang):
        return f"romanized<{text}>"
    monkeypatch.setattr(dictate, "romanize_if_needed", fake_romanize)

    with ws_client.websocket_connect(f"/dictate/stream?lang=hinglish&token={token}") as ws:
        ws.send_bytes(b"\x00" * 100)
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = _drain(ws)

    assert fake_deepgram.options.language == "multi"
    assert fake_deepgram.options.encoding is None     # browser webm: let Deepgram sniff
    partial = next(m for m in msgs if m["type"] == "partial")
    final = next(m for m in msgs if m["type"] == "final")
    assert "romanized" not in partial["text"]         # partials go through raw
    assert final["text"] == "romanized<Hello world.>"


def test_romanize_failure_falls_back_to_raw_text(ws_client, token, fake_deepgram, monkeypatch):
    async def broken(text, lang):
        raise RuntimeError("groq down")
    monkeypatch.setattr(dictate, "romanize_if_needed", broken)

    with ws_client.websocket_connect(f"/dictate/stream?lang=hinglish&token={token}") as ws:
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = _drain(ws)
    assert [m["type"] for m in msgs] == ["final", "done"]
    assert msgs[0]["text"] == "Hello world."


def test_missing_or_bad_token_closes_1008(ws_client, fake_deepgram):
    for qs in ("", "&token=garbage"):
        with ws_client.websocket_connect(f"/dictate/stream?lang=en{qs}") as ws:
            with pytest.raises(Exception) as exc:
                ws.receive_json()
    assert not fake_deepgram.started
    assert "1008" in str(exc.value) or getattr(exc.value, "code", None) == 1008


def test_deepgram_start_failure_closes_1011(ws_client, token, fake_deepgram):
    fake_deepgram.start_ok = False
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as ws:
        err = ws.receive_json()
        assert err["type"] == "error" and "unavailable" in err["text"].lower()
        with pytest.raises(Exception):
            ws.receive_json()


def test_deepgram_send_failure_reports_error(ws_client, token, fake_deepgram):
    fake_deepgram.send_ok = False
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as ws:
        ws.send_bytes(b"\x00" * 10)
        msgs = _drain(ws)
    assert any(m["type"] == "error" for m in msgs)
    assert fake_deepgram.finished                      # still flushed on the way out


def test_duration_cap_flushes_and_keeps_stop_handshake(ws_client, token, fake_deepgram, monkeypatch):
    monkeypatch.setattr(settings, "MAX_DICTATION_SECONDS", 0)
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as ws:
        ws.send_bytes(b"\x00" * 10)        # over the (zero-second) cap immediately
        ws.send_bytes(b"\x00" * 10)        # dropped: already finished
        ws.send_text(json.dumps({"type": "stop"}))
        msgs = _drain(ws)
    types = [m["type"] for m in msgs]
    assert types == ["final", "limit", "done"]
    assert "Press Stop" in msgs[1]["text"]
    assert fake_deepgram.chunks == []     # nothing forwarded after the cap


def test_concurrent_stream_cap(ws_client, token, fake_deepgram, monkeypatch):
    monkeypatch.setattr(settings, "MAX_STREAMS_PER_USER", 1)
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as first:
        with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as second:
            with pytest.raises(Exception) as exc:
                second.receive_json()
        assert "4429" in str(exc.value) or getattr(exc.value, "code", None) == 4429
        first.send_text(json.dumps({"type": "stop"}))
        _drain(first)
    assert dictate._active_streams == {}  # counter released


def test_stream_rate_limit(ws_client, token, fake_deepgram, monkeypatch):
    from app.ratelimit import RateLimiter
    # dictate imported the limiter by name, so patch its binding, not ratelimit's.
    monkeypatch.setattr(dictate, "stream_limiter", RateLimiter(1, 60))
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as ws:
        ws.send_text(json.dumps({"type": "stop"}))
        _drain(ws)
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as ws:
        with pytest.raises(Exception) as exc:
            ws.receive_json()
    assert "4429" in str(exc.value) or getattr(exc.value, "code", None) == 4429


def test_client_disconnect_without_stop_still_flushes(ws_client, token, fake_deepgram):
    with ws_client.websocket_connect(f"/dictate/stream?lang=en&token={token}") as ws:
        ws.send_bytes(b"\x00" * 10)
    # Context exit = client disconnect. The route's cleanup runs on the app's
    # loop after that, so give it a moment rather than racing it.
    import time
    deadline = time.monotonic() + 3
    while not (fake_deepgram.finished and dictate._active_streams == {}) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert fake_deepgram.finished
    assert dictate._active_streams == {}
