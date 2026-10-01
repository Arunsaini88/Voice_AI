# app/routes/dictate.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from app.config import settings
from app.services.romanize import romanize_if_needed
import asyncio, json
from concurrent.futures import Future

router = APIRouter()

DEEPGRAM_LANG_MAP = {
    "en": "en",
    "hi": "hi",
    "hinglish": "multi",   # enables code-switching
}

@router.websocket("/dictate/stream")
async def dictate_stream(
    websocket: WebSocket,
    lang: str = Query("en"),
    # Omit both for containerized audio (browser MediaRecorder webm/opus) and
    # Deepgram sniffs the format. Pass encoding=linear16&sample_rate=16000 for
    # raw PCM with no header (the WAV test client).
    encoding: str | None = Query(None),
    sample_rate: int | None = Query(None),
):
    await websocket.accept()
    loop = asyncio.get_event_loop()

    # Sends scheduled from Deepgram's callback thread. Tracked so the stop
    # handshake can wait for in-flight romanize calls before saying "done".
    pending: set[Future] = set()

    def schedule(coro):
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        pending.add(fut)
        fut.add_done_callback(pending.discard)

    deepgram = DeepgramClient(settings.DEEPGRAM_API_KEY)
    dg_connection = deepgram.listen.websocket.v("1")

    def on_message(self, result, **kwargs):
        transcript = result.channel.alternatives[0].transcript
        if not transcript:
            return

        if result.is_final and lang == "hinglish":
            # Romanize only on final segments — doing this on every partial
            # would add latency to the live-flicker feel for no benefit,
            # since partials get replaced by the final anyway.
            async def send_romanized():
                clean = await romanize_if_needed(transcript, lang)
                await websocket.send_json({"type": "final", "text": clean})
            schedule(send_romanized())
        else:
            payload = {"type": "final" if result.is_final else "partial", "text": transcript}
            schedule(websocket.send_json(payload))

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)

    options = dict(
        model="nova-3",                          # Nova-3, not Nova-2 — built for real-time Hindi/English code-switching
        language=DEEPGRAM_LANG_MAP.get(lang, "en"),
        smart_format=True,
        interim_results=True,
        endpointing=800,
    )
    if encoding:
        options.update(encoding=encoding, sample_rate=sample_rate, channels=1)
    dg_connection.start(LiveOptions(**options))

    finished = False

    async def flush():
        # finish() blocks until Deepgram returns the trailing finals, so run it
        # off-loop; the callbacks it triggers schedule sends back onto the loop.
        nonlocal finished
        if finished:
            return
        finished = True
        await asyncio.to_thread(dg_connection.finish)
        await asyncio.gather(*(asyncio.wrap_future(f) for f in list(pending)), return_exceptions=True)

    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if msg.get("bytes"):
                dg_connection.send(msg["bytes"])
            elif msg.get("text") and json.loads(msg["text"]).get("type") == "stop":
                await flush()
                await websocket.send_json({"type": "done"})
                await websocket.close()
                break
    except WebSocketDisconnect:
        pass
    finally:
        await flush()
