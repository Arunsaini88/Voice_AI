// src/hooks/useDictation.ts
//
// One hook, two engines:
//   browser  MediaRecorder -> WebSocket (this file)
//   desktop  Rust captures the mic and talks to the server; we get events
// Both deliver the same message stream (partial / final / limit / error /
// done), so everything above this hook is engine-agnostic.
import { useEffect, useRef, useState } from "react";
import { apiBase, ensureAccessToken } from "../auth";
import {
  isDesktop,
  onDictationEvent,
  startNativeDictation,
  stopNativeDictation,
  type DictationEvent,
} from "../desktop";

type Language = "en" | "hi" | "hinglish";
type Status = "idle" | "recording" | "finishing";

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
  const statusRef = useRef<Status>("idle");

  const setStatusBoth = (s: Status) => {
    statusRef.current = s;
    setStatus(s);
  };

  const pushSegment = (text: string) => {
    segmentsRef.current = [...segmentsRef.current, text];
    setSegments(segmentsRef.current);
  };

  const cleanup = () => {
    recorderRef.current?.stream.getTracks().forEach((t) => t.stop());
    recorderRef.current = null;
    wsRef.current?.close();
    wsRef.current = null;
    setStatusBoth("idle");
  };

  /** Shared handling of a server message, whichever engine delivered it. */
  const handleMessage = (msg: DictationEvent) => {
    switch (msg.type) {
      case "partial":
        setLiveText(msg.text); // may briefly flash raw script for hinglish
        break;
      case "final":
        pushSegment(msg.text); // already romanized if hinglish
        setLiveText("");
        break;
      case "done":
        onDoneRef.current?.();
        break;
      case "error":
        // Server-side failure (Deepgram down, bad key…). Keep whatever was
        // transcribed so far; the user can still press Stop to save it.
        setError(msg.text);
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
        break;
      case "limit":
        // Server hit the per-session duration cap and already flushed. Stop
        // the mic; the user's next Stop click resolves immediately and saves.
        setError(msg.text);
        if (recorderRef.current?.state === "recording") recorderRef.current.stop();
        if (isDesktop()) void stopNativeDictation();
        break;
      case "close":
        handleClose(msg.code, msg.reason);
        break;
      case "closed":
        // Desktop engine fully ended (after done, error, or server close).
        onDoneRef.current?.();
        if (statusRef.current === "recording") setStatusBoth("idle");
        break;
      default:
        break;
    }
  };

  /** Non-normal close codes carry the reason the server shut us out. */
  const handleClose = (code: number, reason: string): boolean => {
    if (code === 1008) {
      setError(AUTH_EXPIRED); // bad or expired token
      cleanup();
      return true;
    }
    if (code === 1011) {
      setError(reason || "Transcription service unavailable. Please try again later.");
      cleanup();
      return true;
    }
    if (code === 4429) {
      setError(reason || "Too many recordings. Try again in a minute.");
      cleanup();
      return true;
    }
    return false;
  };

  // Desktop: subscribe once to the Rust engine's events.
  useEffect(() => {
    if (!isDesktop()) return;
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    onDictationEvent((e) => handleMessage(e)).then((fn) => {
      if (cancelled) fn();
      else unlisten = fn;
    });
    return () => {
      cancelled = true;
      unlisten?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = async () => {
    if (statusRef.current !== "idle") return;
    segmentsRef.current = [];
    setSegments([]);
    setLiveText("");
    setError(null);

    // The server only checks the token when the socket opens, so make sure it
    // will not expire mid-handshake; a long recording is unaffected afterwards.
    const token = await ensureAccessToken(120);
    if (!token) {
      setError(AUTH_EXPIRED);
      return;
    }

    if (isDesktop()) {
      try {
        await startNativeDictation(apiBase(), token, language);
        setStatusBoth("recording");
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
      return;
    }

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
    setStatusBoth("recording");

    // Token goes in the query string: browsers can't set headers on a WebSocket.
    const wsBase = apiBase().replace(/^http/, "ws");
    const ws = new WebSocket(`${wsBase}/dictate/stream?lang=${language}&token=${encodeURIComponent(token)}`);
    wsRef.current = ws;

    ws.onmessage = (event) => handleMessage(JSON.parse(event.data));
    ws.onclose = (ev) => {
      if (!handleClose(ev.code, ev.reason)) onDoneRef.current?.(); // server went away mid-flush: resolve with what we have
    };
    ws.onerror = () => {
      setError(`Could not reach the backend at ${wsBase} — is it running?`);
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
      if (statusRef.current === "idle") {
        resolve(segmentsRef.current);
        return;
      }
      setStatusBoth("finishing");
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        onDoneRef.current = null;
        cleanup();
        resolve(segmentsRef.current);
      };
      const timer = setTimeout(finish, FLUSH_TIMEOUT_MS + (isDesktop() ? 2000 : 0));
      onDoneRef.current = finish;

      if (isDesktop()) {
        void stopNativeDictation(); // -> Rust sends {"type":"stop"} -> server "done" -> "closed"
      } else if (recorderRef.current && recorderRef.current.state !== "inactive") {
        recorderRef.current.stop(); // -> last ondataavailable -> onstop -> {"type":"stop"} -> server "done"
      } else {
        finish();
      }
    });

  return { liveText, segments, status, error, start, stop };
}
