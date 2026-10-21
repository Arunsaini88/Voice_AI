// src/auth.ts — login token storage and an authenticated fetch wrapper.
export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

const TOKEN_KEY = "voice-notes-token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // private window / storage blocked: user just has to log in each visit
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* same as above */
  }
}

/** Thrown by authFetch on a 401 so callers can send the user back to the login screen. */
export class AuthExpired extends Error {
  constructor() {
    super("Session expired — please log in again");
    this.name = "AuthExpired";
  }
}

/** fetch() with the bearer token attached. A 401 clears the stored token and throws AuthExpired. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API}${path}`, { ...init, headers });
  if (res.status === 401) {
    setToken(null);
    throw new AuthExpired();
  }
  return res;
}

export type AuthMode = "login" | "signup";

/** POST /auth/login or /auth/signup. Resolves with the token; rejects with the server's message. */
export async function authenticate(mode: AuthMode, email: string, password: string): Promise<string> {
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
  setToken(body.access_token);
  return body.access_token as string;
}
