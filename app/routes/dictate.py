# app/routes/dictate.py
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from deepgram import DeepgramClient, LiveTranscriptionEvents, LiveOptions
from app.config import settings
from app.services.romanize import romanize_if_needed
import asyncio

router = APIRouter()

DEEPGRAM_LANG_MAP = {
    "en": "en",
    "hi": "hi",
    "hinglish": "multi",   # enables code-switching
}

@router.websocket("/dictate/stream")
async def dictate_stream(websocket: WebSocket, lang: str = Query("en")):
    await websocket.accept()
    loop = asyncio.get_event_loop()

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
            asyncio.run_coroutine_threadsafe(send_romanized(), loop)
        else:
            payload = {"type": "final" if result.is_final else "partial", "text": transcript}
            asyncio.run_coroutine_threadsafe(websocket.send_json(payload), loop)

    dg_connection.on(LiveTranscriptionEvents.Transcript, on_message)
    dg_connection.start(LiveOptions(
        model="nova-3",                          # Nova-3, not Nova-2 — built for real-time Hindi/English code-switching
        language=DEEPGRAM_LANG_MAP.get(lang, "en"),
        smart_format=True,
        interim_results=True,
        endpointing=800,
    ))

    try:
        while True:
            audio_chunk = await websocket.receive_bytes()
            dg_connection.send(audio_chunk)
    except WebSocketDisconnect:
        dg_connection.finish()