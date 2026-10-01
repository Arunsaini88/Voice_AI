# test_client.py
import asyncio, json, sys, wave, websockets

# usage: python test_client.py [wav] [lang]
#   python test_client.py                                  -> test_audio.wav, en
#   python test_client.py test_audio_hinglish.wav hinglish
WAV  = sys.argv[1] if len(sys.argv) > 1 else "test_audio.wav"
LANG = sys.argv[2] if len(sys.argv) > 2 else "en"
# Raw PCM from wave.readframes() has no header, so tell the server the format.
# A browser sends webm/opus and omits these.
URL  = f"ws://localhost:8000/dictate/stream?lang={LANG}&encoding=linear16&sample_rate=16000"
CHUNK_FRAMES = 4000           # 0.25 s at 16 kHz

async def sender(ws):
    # Pace the audio in real time so Deepgram's endpointing behaves like a live mic.
    with wave.open(WAV, "rb") as wf:
        secs_per_chunk = CHUNK_FRAMES / wf.getframerate()
        while data := wf.readframes(CHUNK_FRAMES):
            await ws.send(data)
            await asyncio.sleep(secs_per_chunk)
    # Same handshake the browser uses: ask the server to flush, then wait for "done".
    await ws.send(json.dumps({"type": "stop"}))

async def receiver(ws):
    try:
        async for msg in ws:
            evt = json.loads(msg)
            if evt["type"] == "done":
                print("[done   ] server flushed")
                return
            tag = "FINAL  " if evt["type"] == "final" else "partial"
            print(f"[{tag}] {evt['text']}")
    except websockets.ConnectionClosed:
        pass

async def test():
    async with websockets.connect(URL) as ws:
        await asyncio.gather(sender(ws), receiver(ws))

asyncio.run(test())
