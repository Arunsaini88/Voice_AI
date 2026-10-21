// src/Login.tsx
import { useState, type FormEvent } from "react";
import { authenticate, type AuthMode } from "./auth";

export default function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLogin(await authenticate(mode, email, password));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ maxWidth: 360, margin: "80px auto", fontFamily: "sans-serif" }}>
      <h1 style={{ fontSize: 22 }}>Voice Notes</h1>
      <form onSubmit={submit} style={{ display: "grid", gap: 10 }}>
        <input
          type="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="email"
          required
        />
        <input
          type="password"
          placeholder={mode === "signup" ? "Password (8+ characters)" : "Password"}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
          minLength={8}
          required
        />
        <button type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
        </button>
      </form>
      {error && <p style={{ color: "crimson" }}>{error}</p>}
      <p style={{ fontSize: 14, opacity: 0.7 }}>
        {mode === "login" ? "No account yet? " : "Already have an account? "}
        <button
          type="button"
          onClick={() => { setMode(mode === "login" ? "signup" : "login"); setError(null); }}
          style={{ background: "none", border: "none", padding: 0, color: "inherit", textDecoration: "underline", cursor: "pointer" }}
        >
          {mode === "login" ? "Sign up" : "Log in"}
        </button>
      </p>
    </div>
  );
}
