// src/App.tsx
import { useCallback, useEffect, useState } from "react";
import { useDictation } from "./hooks/useDictation";

type Language = "en" | "hi" | "hinglish";

type Note = {
  id: string;
  title: string | null;
  summary: string | null;
  key_points: string[] | null;
  language: string | null;
  raw_transcript: string;
  created_at: string;
};

const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";
// Static token the backend accepts while DEV_TOKEN is set in its .env. Replace with a real login later.
const AUTH = { Authorization: `Bearer ${(import.meta.env.VITE_DEV_TOKEN as string | undefined) ?? "dev-token-123"}` };

export default function App() {
  const [language, setLanguage] = useState<Language>("en");
  const { liveText, segments, status, error, start, stop } = useDictation(language);
  const [notes, setNotes] = useState<Note[]>([]);
  const [saving, setSaving] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const loadNotes = useCallback(async () => {
    try {
      const res = await fetch(`${API}/notes`, { headers: AUTH });
      if (!res.ok) throw new Error(`GET /notes ${res.status}`);
      setNotes(await res.json());
    } catch (e) {
      setApiError(String(e));
    }
  }, []);

  useEffect(() => { void loadNotes(); }, [loadNotes]);

  const handleStop = async () => {
    const finalSegments = await stop(); // resolves after the server flushes the last sentence
    const transcript = finalSegments.join(" ").trim();
    if (!transcript) return;

    setSaving(true);
    setApiError(null);
    try {
      const res = await fetch(`${API}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...AUTH },
        body: JSON.stringify({ transcript, language }),
      });
      if (!res.ok) throw new Error(`POST /notes ${res.status}`);
      const note: Note = await res.json();
      setNotes((prev) => [note, ...prev]);
    } catch (e) {
      setApiError(String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: string) => {
    const res = await fetch(`${API}/notes/${id}`, { method: "DELETE", headers: AUTH });
    if (res.ok) setNotes((prev) => prev.filter((n) => n.id !== id));
    else setApiError(`DELETE /notes/${id} ${res.status}`);
  };

  return (
    <div style={{ maxWidth: 600, margin: "40px auto", fontFamily: "sans-serif" }}>
      <select value={language} onChange={(e) => setLanguage(e.target.value as Language)} disabled={status !== "idle"}>
        <option value="en">English</option>
        <option value="hi">Hindi</option>
        <option value="hinglish">Hinglish</option>
      </select>

      <button onClick={status === "idle" ? () => start() : handleStop} disabled={status === "finishing" || saving}>
        {status === "idle" ? "Start" : status === "finishing" ? "Finishing…" : "Stop"}
      </button>

      {error && <p style={{ color: "crimson" }}>{error}</p>}
      {apiError && <p style={{ color: "crimson" }}>{apiError}</p>}

      <p style={{ minHeight: 100, whiteSpace: "pre-wrap" }}>
        {segments.join(" ")} <span style={{ opacity: 0.5 }}>{liveText}</span>
      </p>

      {saving && <p>Summarizing & saving…</p>}

      <h2 style={{ fontSize: 18, marginTop: 32 }}>Notes</h2>
      {notes.length === 0 && !saving && <p style={{ opacity: 0.6 }}>No notes yet.</p>}
      {notes.map((n) => (
        <article key={n.id} style={{ borderTop: "1px solid #ddd", padding: "12px 0" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
            <h3 style={{ margin: 0 }}>{n.title ?? "(untitled)"}</h3>
            <button onClick={() => handleDelete(n.id)} aria-label="Delete note">✕</button>
          </div>
          <small style={{ opacity: 0.6 }}>
            {new Date(n.created_at).toLocaleString()} · {n.language}
          </small>
          <p>{n.summary}</p>
          {n.key_points && <ul>{n.key_points.map((p, i) => <li key={i}>{p}</li>)}</ul>}
        </article>
      ))}
    </div>
  );
}
