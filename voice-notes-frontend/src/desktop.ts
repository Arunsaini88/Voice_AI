// src/desktop.ts — bridge to the Tauri shell. Every function is a no-op or
// returns a sensible default in the plain browser, so the same bundle serves
// both the web app and the desktop app.
import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export function isDesktop(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export type DictationEvent =
  | { type: "partial" | "final" | "limit" | "error" | "raw"; text: string }
  | { type: "done" }
  | { type: "closed" }
  | { type: "close"; code: number; reason: string };

/** Start the native microphone -> backend stream. Resolves once the session thread is running. */
export function startNativeDictation(apiUrl: string, token: string, lang: string): Promise<void> {
  return invoke("start_dictation", { apiUrl, token, lang });
}

export function stopNativeDictation(): Promise<void> {
  return invoke("stop_dictation");
}

export function onDictationEvent(handler: (e: DictationEvent) => void): Promise<UnlistenFn> {
  return listen<DictationEvent>("dictation", (ev) => handler(ev.payload));
}

/** Global hotkey (Ctrl+Shift+M) pressed anywhere. */
export function onHotkey(handler: () => void): Promise<UnlistenFn> {
  return listen("hotkey", () => handler());
}

/** Paste text into the window that has keyboard focus. Hide ours first. */
export function pasteIntoFocusedApp(text: string): Promise<void> {
  return invoke("paste_text", { text });
}

export function hideWindow(): Promise<void> {
  return invoke("hide_window");
}

export function microphoneName(): Promise<string | null> {
  return invoke<string | null>("mic_name");
}
