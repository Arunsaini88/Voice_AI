// src/Login.tsx
import { useState, type FormEvent } from "react";
import { authenticate, type AuthMode } from "./auth";
import Brand from "./Brand";

export default function Login({ onLogin, onForgot }: { onLogin: () => void; onForgot: () => void }) {
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
      await authenticate(mode, email, password);
      onLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth">
      <div className="auth-card">
        <Brand />
        <p className="auth-sub">Dictate in English, Hindi, or Hinglish. Get a summary you can keep.</p>

        <form onSubmit={submit} className="form">
          <label className="field">
            Email
            <input
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              required
            />
          </label>
          <label className="field">
            Password
            <input
              className="input"
              type="password"
              placeholder={mode === "signup" ? "At least 8 characters" : ""}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete={mode === "signup" ? "new-password" : "current-password"}
              minLength={8}
              required
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
          </button>
        </form>

        {error && <p className="alert alert-error" role="alert">{error}</p>}

        {mode === "login" && (
          <p className="auth-switch">
            <button type="button" className="btn-link" onClick={onForgot}>Forgot password?</button>
          </p>
        )}
        <p className="auth-switch">
          {mode === "login" ? "No account yet? " : "Already have an account? "}
          <button
            type="button"
            className="btn-link"
            onClick={() => { setMode(mode === "login" ? "signup" : "login"); setError(null); }}
          >
            {mode === "login" ? "Sign up" : "Log in"}
          </button>
        </p>
      </div>
    </div>
  );
}
