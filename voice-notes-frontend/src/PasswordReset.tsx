// src/PasswordReset.tsx — the two halves of "forgot my password".
import { useState, type FormEvent } from "react";
import { requestPasswordReset, resetPassword } from "./auth";
import Brand from "./Brand";

/** Step 1: ask for the email. Shown from the login card. */
export function ForgotPassword({ onBack }: { onBack: () => void }) {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setMessage(await requestPasswordReset(email));
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
        <p className="auth-sub">Enter your email and we'll send a link to choose a new password.</p>

        {message ? (
          <p className="alert alert-info" role="status">{message}</p>
        ) : (
          <form onSubmit={submit} className="form">
            <label className="field">
              Email
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" required />
            </label>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              {busy ? "Sending…" : "Send reset link"}
            </button>
          </form>
        )}

        {error && <p className="alert alert-error" role="alert">{error}</p>}

        <p className="auth-switch">
          <button type="button" className="btn-link" onClick={onBack}>Back to log in</button>
        </p>
      </div>
    </div>
  );
}

/** Step 2: the page the emailed link opens, /reset-password?token=… */
export function ResetPassword({ token, onDone }: { token: string; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await resetPassword(token, password);
      setDone(true);
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
        {done ? (
          <>
            <p className="alert alert-info" role="status">Password updated. You have been logged out everywhere.</p>
            <p className="auth-switch">
              <button type="button" className="btn-link" onClick={onDone}>Log in with your new password</button>
            </p>
          </>
        ) : (
          <>
            <p className="auth-sub">Choose a new password.</p>
            <form onSubmit={submit} className="form">
              <label className="field">
                New password
                <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={8} required placeholder="At least 8 characters" />
              </label>
              <label className="field">
                Confirm password
                <input className="input" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" minLength={8} required />
              </label>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                {busy ? "Saving…" : "Set new password"}
              </button>
            </form>
            {error && <p className="alert alert-error" role="alert">{error}</p>}
            {!token && <p className="alert alert-error">This link is missing its token. Request a new one.</p>}
            <p className="auth-switch">
              <button type="button" className="btn-link" onClick={onDone}>Back to log in</button>
            </p>
          </>
        )}
      </div>
    </div>
  );
}
