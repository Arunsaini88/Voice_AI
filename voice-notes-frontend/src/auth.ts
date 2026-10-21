// src/auth.ts — session handling: short-lived access token in memory,
// long-lived refresh token in localStorage, silent refresh on 401.
export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

const REFRESH_KEY = "voice-notes-refresh";
let accessToken: string | null = null;

type TokenPair = { access_token: string; refresh_token: string };

function readRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    return null; // private window / storage blocked: user just has to log in each visit
  }
}

function storeSession(pair: TokenPair) {
  accessToken = pair.access_token;
  try {
    localStorage.setItem(REFRESH_KEY, pair.refresh_token);
  } catch {
    /* see readRefresh */
  }
}

export function clearSession() {
  accessToken = null;
  try {
    localStorage.removeItem(REFRESH_KEY);
  } catch {
    /* see readRefresh */
  }
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Seconds until the access token expires, or 0 if there is none / it is unreadable. */
export function accessTokenSecondsLeft(): number {
  if (!accessToken) return 0;
  try {
    const payload = JSON.parse(atob(accessToken.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return Math.max(0, Math.floor(payload.exp - Date.now() / 1000));
  } catch {
    return 0;
  }
}

/** Thrown when the session cannot be refreshed; callers send the user to the login screen. */
export class AuthExpired extends Error {
  constructor() {
    super("Session expired — please log in again");
    this.name = "AuthExpired";
  }
}

// Single-flight: concurrent 401s share one refresh call, so a rotated token is
// never presented twice (which the server would treat as theft).
let inflight: Promise<string | null> | null = null;

/** Trade the stored refresh token for a new pair. Resolves with the new access token, or null if the session is gone. */
export function refreshSession(): Promise<string | null> {
  if (inflight) return inflight;
  inflight = (async () => {
    const refresh = readRefresh();
    if (!refresh) return null;
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refresh }),
      });
      if (!res.ok) {
        if (res.status === 401) clearSession(); // revoked / expired / replayed: start over
        return null;                            // 5xx or network: keep tokens, caller decides
      }
      const pair: TokenPair = await res.json();
      storeSession(pair);
      return pair.access_token;
    } catch {
      return null;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/** On page load: turn a stored refresh token into a live session. */
export async function restoreSession(): Promise<boolean> {
  if (!readRefresh()) return false;
  return (await refreshSession()) !== null;
}

/** Access token guaranteed to last at least `minSeconds` more, refreshing if needed. Null = logged out. */
export async function ensureAccessToken(minSeconds = 60): Promise<string | null> {
  if (accessToken && accessTokenSecondsLeft() > minSeconds) return accessToken;
  return refreshSession();
}

/** fetch() with the bearer token. On 401 it refreshes once and retries; if that fails it throws AuthExpired. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string) => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    return fetch(`${API}${path}`, { ...init, headers });
  };

  let token = await ensureAccessToken();
  if (!token) throw new AuthExpired();
  let res = await send(token);
  if (res.status !== 401) return res;

  token = await refreshSession();
  if (!token) throw new AuthExpired();
  res = await send(token);
  if (res.status === 401) {
    clearSession();
    throw new AuthExpired();
  }
  return res;
}

export type AuthMode = "login" | "signup";

/** POST /auth/login or /auth/signup. Stores the session; rejects with the server's message. */
export async function authenticate(mode: AuthMode, email: string, password: string): Promise<void> {
  const res = await fetch(`${API}/auth/${mode}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // FastAPI: validation errors arrive as {detail: [{msg}]}, others as {detail: "..."}
    const detail = body.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => String(d.msg ?? "").replace(/^Value error, /, "")).join("; ")
      : typeof detail === "string"
        ? detail
        : `${mode} failed (${res.status})`;
    throw new Error(msg);
  }
  storeSession(body as TokenPair);
}

/** Revoke the refresh token server-side (best effort), then forget the session locally. */
export async function logout(): Promise<void> {
  const refresh = readRefresh();
  clearSession();
  if (!refresh) return;
  try {
    await fetch(`${API}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      keepalive: true,
    });
  } catch {
    /* offline: the token expires on its own */
  }
}
