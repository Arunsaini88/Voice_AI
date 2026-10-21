// src/hooks/useDictation.ts
import { useRef, useState } from "react";
import { API, ensureAccessToken } from "../auth";

type Language = "en" | "hi" | "hinglish";
type Status = "idle" | "recording" | "finishing";

const WS_BASE = API.replace(/^http/, "ws");
const FLUSH_TIMEOUT_MS = 4000; // never let stop() hang if the server goes quiet
/** Sentinel error value: the server rejected the login token. App turns this into a logout. */
export const AUTH_EXPIRED = "AUTH_EXPIRED";

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

    // The server only checks the token when the socket opens, so make sure it
    // will not expire mid-handshake; a long recording is unaffected afterwards.
    const token = await ensureAccessToken(120);
    if (!token) {
      setError(AUTH_EXPIRED);
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

    // Token goes in the query string: browsers can't set headers on a WebSocket.
    const ws = new WebSocket(
      `${WS_BASE}/dictate/stream?lang=${language}&token=${encodeURIComponent(token)}`,
    );
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type === "partial") setLiveText(msg.text); // may briefly flash raw script for hinglish
      if (msg.type === "final") {
        pushSegment(msg.text); // already romanized if hinglish
        setLiveText("");
      }
      if (msg.type === "done") onDoneRef.current?.();
      if (msg.type === "error") {
        // Server-side failure (Deepgram down, bad key…). Keep whatever was
        // transcribed so far; the user can still press Stop to save it.
        setError(msg.text);
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      }
      if (msg.type === "limit") {
        // Server hit the per-session duration cap and already flushed. Stop
        // the mic; the user's next Stop click resolves immediately and saves.
        setError(msg.text);
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      }
    };
    ws.onclose = (ev) => {
      if (ev.code === 1008) {
        // Server accepted then closed with "policy violation": bad or expired token.
        setError(AUTH_EXPIRED);
        cleanup();
        return;
      }
      if (ev.code === 1011) {
        // Server could not reach the transcription service.
        setError(ev.reason || "Transcription service unavailable. Please try again later.");
        cleanup();
        return;
      }
      if (ev.code === 4429) {
        // App-defined: rate limit or too many concurrent recordings.
        setError(ev.reason || "Too many recordings. Try again in a minute.");
        cleanup();
        return;
      }
      onDoneRef.current?.(); // server went away mid-flush: resolve with what we have
    };
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
