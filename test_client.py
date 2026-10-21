# test_client.py
import asyncio, json, os, sys, urllib.error, urllib.request, wave, websockets

# Hindi / Devanagari output would crash on a cp1252 Windows console otherwise.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# usage: python test_client.py [wav] [lang]
#   python test_client.py                                  -> test_audio.wav, en
#   python test_client.py test_audio_hinglish.wav hinglish
# Logs in as VN_EMAIL / VN_PASSWORD (env vars; defaults below), creating the
# account on first use, since /dictate/stream now requires a login token.
WAV  = sys.argv[1] if len(sys.argv) > 1 else "test_audio.wav"
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
API  = os.environ.get("VN_API", "http://localhost:8000")
EMAIL    = os.environ.get("VN_EMAIL", "test-client@example.com")
PASSWORD = os.environ.get("VN_PASSWORD", "test-client-pass-123")
CHUNK_FRAMES = 4000           # 0.25 s at 16 kHz


def _post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{API}{path}", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def get_token() -> str:
    creds = {"email": EMAIL, "password": PASSWORD}
    status, body = _post("/auth/login", creds)
    if status == 401:
        status, body = _post("/auth/signup", creds)
        print(f"[auth   ] created account {EMAIL}")
    if status not in (200, 201):
        sys.exit(f"auth failed ({status}): {body}")
    return body["access_token"]


async def sender(ws):
    # Pace the audio in real time so Deepgram's endpointing behaves like a live mic.
    try:
        with wave.open(WAV, "rb") as wf:
            secs_per_chunk = CHUNK_FRAMES / wf.getframerate()
            while data := wf.readframes(CHUNK_FRAMES):
                await ws.send(data)
                await asyncio.sleep(secs_per_chunk)
        # Same handshake the browser uses: ask the server to flush, then wait for "done".
        await ws.send(json.dumps({"type": "stop"}))
    except websockets.ConnectionClosed:
        pass  # server closed early (error/limit); receiver prints the reason


async def receiver(ws):
    try:
        async for msg in ws:
            evt = json.loads(msg)
            if evt["type"] == "done":
                print("[done   ] server flushed")
                return
            tag = {"final": "FINAL  ", "partial": "partial"}.get(evt["type"], evt["type"].ljust(7))
            print(f"[{tag}] {evt['text']}")
    except websockets.ConnectionClosed as e:
        print(f"[closed ] {e.code} {e.reason}")


async def test():
    token = get_token()
    # Raw PCM from wave.readframes() has no header, so tell the server the format.
    # A browser sends webm/opus and omits these.
    url = (
        f"{API.replace('http', 'ws', 1)}/dictate/stream"
        f"?lang={LANG}&encoding=linear16&sample_rate=16000&token={token}"
    )
    async with websockets.connect(url) as ws:
        await asyncio.gather(sender(ws), receiver(ws))


asyncio.run(test())
