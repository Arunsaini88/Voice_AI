// src/hooks/useDictation.ts
import { useRef, useState } from "react";

type Language = "en" | "hi" | "hinglish";
type Status = "idle" | "recording" | "finishing";

const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
const WS_BASE = API.replace(/^http/, "ws");
const FLUSH_TIMEOUT_MS = 4000; // never let stop() hang if the server goes quiet

export function useDictation(language: Language) {
  const [liveText, setLiveText] = useState("");
  const [segments, setSegments] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const segmentsRef = useRef<string[]>([]);          // state mirror, readable synchronously inside stop()
  const onDoneRef = useRef<(() => void) | null>(null);

  const pushSegment = (text: string) => {
    segmentsRef.current = [...segmentsRef.current, text];
    setSegments(segmentsRef.current);
  };

  const cleanup = () => {
    recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setStatus("idle");
  };

  const start = async () => {
    segmentsRef.current = [];
    setSegments([]);
    setLiveText("");
    setError(null);

    // getUserMedia only exists in a secure context: https://, or http://localhost.
    // Opening the page via a LAN IP (http://192.168.x.x:5173) leaves it undefined.
    if (!navigator.mediaDevices?.getUserMedia) {
      setError(`Microphone API unavailable — open the page via http://localhost, not ${location.host}`);
      return;
    }

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      const name = e instanceof DOMException ? e.name : String(e);
      setError(
        name === "NotAllowedError"
          ? "Microphone blocked — click the camera/mic icon in the address bar and allow it"
          : name === "NotFoundError"
            ? "No microphone found"
            : `Microphone error: ${name}`,
      );
      return;
    }
    setStatus("recording");

    const ws = new WebSocket(`${WS_BASE}/dictate/stream?lang=${language}`);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "partial") setLiveText(msg.text); // may briefly flash raw script for hinglish
      if (msg.type === "final") {
        pushSegment(msg.text); // already romanized if hinglish
        setLiveText("");
      }
      if (msg.type === "done") onDoneRef.current?.();
    };
    ws.onclose = () => onDoneRef.current?.(); // server went away mid-flush: resolve with what we have
    ws.onerror = () => {
      setError(`Could not reach the backend at ${WS_BASE} — is uvicorn running?`);
      cleanup();
    };

    ws.onopen = () => {
      const recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      recorderRef.current = recorder;

      // Blob -> ArrayBuffer is async, so chain sends to keep audio in order and
      // make sure the last chunk goes out *before* the stop message.
      let sendChain: Promise<unknown> = Promise.resolve();
      recorder.ondataavailable = (e) => {
        if (e.data.size === 0) return;
        sendChain = sendChain
          .then(() => e.data.arrayBuffer())
          .then((buf) => { if (ws.readyState === WebSocket.OPEN) ws.send(buf); });
      };
      recorder.onstop = () => {
        sendChain.then(() => { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" })); });
      };
      recorder.start(250); // send a chunk every 250ms
    };
  };

  /** Stops the mic, waits for the server to flush the trailing final, resolves with the full transcript. */
  const stop = (): Promise<string[]> =>
    new Promise((resolve) => {
      setStatus("finishing");
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        onDoneRef.current = null;
        cleanup();
        resolve(segmentsRef.current);
      };
      const timer = setTimeout(finish, FLUSH_TIMEOUT_MS);
      onDoneRef.current = finish;

      if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop(); // -> last ondataavailable -> onstop -> {"type":"stop"} -> server "done"
      } else {
        finish();
      }
    });

  return { liveText, segments, status, error, start, stop };
}
