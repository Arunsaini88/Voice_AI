# app/routes/dictate.py
import asyncio, json, logging, time
from concurrent.futures import Future
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from app.config import settings
from app.deps import user_from_token
from app import ratelimit
from app.services.romanize import romanize_if_needed

router = APIRouter()
log = logging.getLogger("app.dictate")

DEEPGRAM_LANG_MAP = {
    "en": "en",
    "hi": "hi",
    "hinglish": "multi",   # enables code-switching
}

# WebSocket close codes. 1008 = policy violation and 1011 = server error are
# standard; 4429 is app-defined "too many" so the frontend can tell a limit
# from an expired login.
WS_UNAUTHORIZED = 1008
WS_SERVER_ERROR = 1011
WS_TOO_MANY = 4429

@router.websocket("/dictate/stream")
async def dictate_stream(
    websocket: WebSocket,
    # Login token from POST /auth/login. Browsers can't set Authorization on a
    # WebSocket, so it rides in the query string.
    token: str = Query(""),
    lang: str = Query("en"),
    # Omit both for containerized audio (browser MediaRecorder webm/opus) and
    # Deepgram sniffs the format. Pass encoding=linear16&sample_rate=16000 for
    # raw PCM with no header (the WAV test client).
    encoding: str | None = Query(None),
    sample_rate: int | None = Query(None),
):
    await websocket.accept()
    # Accept first, then close with a code so the browser sees it; a pre-accept
    # rejection surfaces as a generic connection failure.
    user = user_from_token(token)
    if user is None:
        await websocket.close(code=WS_UNAUTHORIZED, reason="Unauthorized")
        return
    uid = user["id"]
    if await ratelimit.stream_limiter.hit(uid) is not None:
        log.warning("stream rate-limited", extra={"user_id": uid})
        await websocket.close(code=WS_TOO_MANY, reason="Too many recordings started. Wait a minute.")
        return
    # One slot per open session, so one account can't fan out N Deepgram streams.
    if not await ratelimit.stream_slots.acquire(uid, settings.MAX_STREAMS_PER_USER):
        log.warning("stream concurrency-limited", extra={"user_id": uid})
        await websocket.close(code=WS_TOO_MANY, reason="Too many recordings open at once")
        return

    try:
        await _run_stream(websocket, uid, lang, encoding, sample_rate)
    finally:
        await ratelimit.stream_slots.release(uid)


async def _run_stream(websocket: WebSocket, uid: str, lang: str, encoding: str | None, sample_rate: int | None):
    loop = asyncio.get_event_loop()
    started = time.monotonic()
    stats = {"audio_bytes": 0, "finals": 0}

    # Sends scheduled from Deepgram's callback thread. Tracked so the stop
    # handshake can wait for in-flight romanize calls before saying "done".
    pending: set[Future] = set()

    async def safe_send(payload: dict):
        # The client may already be gone; that's not worth a traceback.
        try:
            await websocket.send_json(payload)
        except Exception as e:  # noqa: BLE001
            log.debug("send after close dropped: %s", e, extra={"user_id": uid})

    def schedule(coro):
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        pending.add(fut)
        fut.add_done_callback(pending.discard)

    deepgram = DeepgramClient(settings.DEEPGRAM_API_KEY)
    dg_connection = deepgram.listen.websocket.v("1")

    # --- Deepgram callbacks. These run on the SDK's thread: anything raised
    # here would die silently, so each one catches and logs. ---

    def on_message(self, result, **kwargs):
        try:
            transcript = result.channel.alternatives[0].transcript
        except Exception:  # noqa: BLE001
            log.exception("unexpected Deepgram transcript shape", extra={"user_id": uid})
            return
        if not transcript:
            return

        if result.is_final:
            stats["finals"] += 1

        if result.is_final and lang == "hinglish":
            # Romanize only on final segments — doing this on every partial
            # would add latency to the live-flicker feel for no benefit,
            # since partials get replaced by the final anyway.
            async def send_romanized():
                try:
                    clean = await romanize_if_needed(transcript, lang)
                except Exception:  # noqa: BLE001
                    # Groq down or model retired: the user still gets their words,
                    # just in the original script, and we hear about it in the log.
                    log.exception("romanize failed; sending raw transcript", extra={"user_id": uid})
                    clean = transcript
                await safe_send({"type": "final", "text": clean})
            schedule(send_romanized())
        else:
            payload = {"type": "final" if result.is_final else "partial", "text": transcript}
            schedule(safe_send(payload))

    def on_error(self, error, **kwargs):
        log.error("Deepgram error: %s", error, extra={"user_id": uid, "lang": lang})
        schedule(safe_send({"type": "error", "text": "Transcription service error. Please try again."}))

    def on_warning(self, warning, **kwargs):
        log.warning("Deepgram warning: %s", warning, extra={"user_id": uid})

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
    dg_connection.on(LiveTranscriptionEvents.Error, on_error)
    dg_connection.on(LiveTranscriptionEvents.Warning, on_warning)

    options = dict(
        model="nova-3",                          # Nova-3, not Nova-2 — built for real-time Hindi/English code-switching
        language=DEEPGRAM_LANG_MAP.get(lang, "en"),
        smart_format=True,
        interim_results=True,
        endpointing=800,
    )
    if encoding:
        options.update(encoding=encoding, sample_rate=sample_rate, channels=1)

    # start() returns False instead of raising on a bad key, network failure,
    # or rejected options. Fail loudly rather than stream audio into nothing.
    if not await asyncio.to_thread(dg_connection.start, LiveOptions(**options)):
        log.error("Deepgram connection failed to start (check DEEPGRAM_API_KEY / options)",
                  extra={"user_id": uid, "lang": lang})
        await safe_send({"type": "error", "text": "Transcription service unavailable. Please try again later."})
        await websocket.close(code=WS_SERVER_ERROR, reason="Transcription service unavailable")
        return

    log.info("stream open", extra={"user_id": uid, "lang": lang, "event": "stream_open"})
    finished = False

    async def flush():
        # finish() blocks until Deepgram returns the trailing finals, so run it
        # off-loop; the callbacks it triggers schedule sends back onto the loop.
        nonlocal finished
        if finished:
            return
        finished = True
        try:
            await asyncio.to_thread(dg_connection.finish)
        except Exception:  # noqa: BLE001
            log.exception("Deepgram finish failed", extra={"user_id": uid})
        await asyncio.gather(*(asyncio.wrap_future(f) for f in list(pending)), return_exceptions=True)

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                if finished:
                    continue  # past the duration cap: drop audio, still honour "stop"
                if time.monotonic() - started >= settings.MAX_DICTATION_SECONDS:
                    # Cap one session's Deepgram minutes. Flush what was said so
                    # far, then tell the client; it keeps the normal stop flow.
                    await flush()
                    secs = settings.MAX_DICTATION_SECONDS
                    cap = f"{secs // 60} min" if secs >= 60 else f"{secs} s"
                    log.info("stream hit duration cap", extra={"user_id": uid, "event": "stream_capped"})
                    await safe_send({"type": "limit", "text": f"Recording limit of {cap} reached. Press Stop to save."})
                    continue
                stats["audio_bytes"] += len(msg["bytes"])
                if not dg_connection.send(msg["bytes"]):
                    log.error("Deepgram rejected audio chunk; closing", extra={"user_id": uid})
                    await flush()
                    await safe_send({"type": "error", "text": "Transcription service error. Please try again."})
                    await websocket.close(code=WS_SERVER_ERROR, reason="Transcription service error")
                    break
            elif msg.get("text") and json.loads(msg["text"]).get("type") == "stop":
                await flush()
                await safe_send({"type": "done"})
                await websocket.close()
                break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("stream crashed", extra={"user_id": uid})
        await safe_send({"type": "error", "text": "Recording failed on the server."})
        try:
            await websocket.close(code=WS_SERVER_ERROR, reason="Recording failed")
        except Exception:  # noqa: BLE001
            pass
    finally:
        await flush()
        log.info(
            "stream closed",
            extra={"user_id": uid, "lang": lang, "event": "stream_close",
                   "duration_s": round(time.monotonic() - started, 1), **stats},
        )
