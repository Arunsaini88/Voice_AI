# Development guide

How the pieces fit, where the sharp edges are, and how to work on this without a microphone.

## Architecture

```
Browser (React)                      FastAPI                         External
──────────────                       ───────                         ────────
MediaRecorder ──webm/opus chunks──▶ /dictate/stream ──▶ Deepgram live (nova-3)
              ◀── partial / final ──          ▲   ◀── transcript callbacks (SDK thread)
                                              └── hinglish finals ──▶ Groq (romanize)
Stop ──{"type":"stop"}──▶ flush ──▶ done
POST /notes {transcript} ──────────▶ summarize ──▶ Groq (JSON) ──▶ Postgres (notes)
```

Backend layout, `voice-notes-backend/app/`:

| File | Role |
|---|---|
| `main.py` | App factory, CORS, router registration, startup log |
| `config.py` | Every setting, read from env or `.env` |
| `db.py` | Async engine. Rewrites Neon URLs for asyncpg. SQLite path is for tests. Startup schema check |
| `../alembic/` | Migrations. `env.py` reuses the app engine, so `DATABASE_URL` is the only config |
| `models.py` | `users`, `notes` |
| `deps.py` | JWT create and decode, `get_current_user` |
| `schemas.py` | Shared request shape for anything carrying a transcript |
| `ratelimit.py` | In-process sliding-window limiters and their dependencies |
| `logging_config.py` | Text or JSON formatter; uvicorn routed through it |
| `routes/auth.py` | Signup, login, me |
| `routes/dictate.py` | The WebSocket |
| `routes/notes.py` | CRUD plus summarize-on-create |
| `routes/summarizer.py` | Stateless summary preview |
| `services/llm.py` | One shared Groq client |
| `services/tokens.py` | Refresh-token rotation and reuse detection; reset tokens |
| `services/mailer.py` | Outbound email: console or SMTP |
| `services/romanize.py` | Devanagari to typed Hinglish |
| `services/summarize.py` | Title, summary, key points; output coerced to a fixed shape |

Frontend, `voice-notes-frontend/src/`:

| File | Role |
|---|---|
| `App.tsx` | Login gate, language picker, record button, notes list |
| `Login.tsx` | Signup and login form |
| `PasswordReset.tsx` | Forgot-password form and the reset page the emailed link opens |
| `Brand.tsx` | App name and microphone mark |
| `auth.ts` | Token storage, `authFetch` wrapper that logs out on 401 |
| `hooks/useDictation.ts` | Mic capture, WebSocket, stop handshake, error mapping |
| `index.css` | Color tokens (light and dark), base typography |
| `App.css` | Component styles: top bar, controls, transcript, notes, auth card |

## Dictation protocol

Client connects to `/dictate/stream?lang=<en|hi|hinglish>&token=<jwt>`. Browsers cannot set headers on a WebSocket, so the token is a query parameter. Two optional parameters, `encoding=linear16&sample_rate=16000`, tell Deepgram the audio is raw PCM; omit them for browser webm/opus and Deepgram sniffs the container.

Client sends binary frames of audio, then one text frame `{"type":"stop"}`.

Server sends JSON text frames:

| `type` | Meaning |
|---|---|
| `partial` | Interim text. Replaces the previous partial. Hinglish partials are raw Devanagari |
| `final` | A finished segment. Hinglish finals are romanized |
| `limit` | Session hit `MAX_DICTATION_SECONDS`. Audio after this is dropped. Client should stop the mic; a later `stop` still gets `done` |
| `error` | Deepgram failed. Client keeps what it has |
| `done` | All trailing finals delivered after `stop`. Server closes next |

Close codes:

| Code | Meaning | Frontend reaction |
|---|---|---|
| 1000 | Normal, after `done` | Resolve with collected segments |
| 1008 | Bad or expired token | Log out |
| 1011 | Deepgram unavailable or stream crashed | Show reason |
| 4429 | Rate limit or concurrent-stream cap | Show reason |

The server accepts the socket before checking the token so the browser sees a close code; rejecting before accept shows up as a generic connection error.

### Why the stop handshake exists

The last sentence is usually still inside Deepgram when the user presses Stop. The server calls `finish()` on Deepgram, which blocks until trailing finals arrive, so it runs in a thread. Hinglish finals then go through Groq. The server tracks every send it has scheduled and waits for them before sending `done`. The frontend chains its audio chunk sends so the last chunk goes out before `stop`, and gives up after 4 seconds if `done` never comes.

### Threading

Deepgram's SDK invokes callbacks on its own thread. Anything raised there is lost, so each callback catches and logs, and all sends are marshalled onto the event loop with `run_coroutine_threadsafe`. If romanize finals ever "stop arriving" in Hinglish mode, check the log for `romanize failed` before reading code; it is nearly always Groq (model retired, quota).

## Auth

- Passwords are bcrypt hashed in a thread. Length 8 to 72 bytes, because bcrypt ignores everything past 72.
- Login verifies against a dummy hash when the email is unknown so timing cannot reveal which accounts exist. Same 401 message either way.
- **Access tokens** are HS256 JWTs with `sub` = user id and a 30-minute expiry. They are verified by signature only, so they cannot be revoked; the short life is the mitigation.
- **Refresh tokens** are random strings whose SHA-256 is stored in `refresh_tokens` (`services/tokens.py`). `POST /auth/refresh` rotates: the presented token is revoked and a new one issued in the same `family_id`. Presenting a revoked token again is treated as theft and revokes the whole family. `POST /auth/logout` revokes one token; `revoke_all_for_user` (for password reset) revokes everything.
- The frontend keeps the access token in memory and the refresh token in localStorage. `authFetch` retries once after a refresh on 401; the dictation hook refreshes before connecting if the access token has under two minutes left. Moving the refresh token to an httpOnly cookie is a contained change in `auth.ts` plus the refresh route, and needs the frontend and API on one parent domain.
- `notes.user_id` and `refresh_tokens.user_id` are foreign keys to `users.id` with cascade delete (migration 0002).
- **Password reset.** `POST /auth/forgot-password` always answers 202 with one fixed message and sends the mail from a background task, so neither the body nor the timing reveals whether the address exists. The link carries a random token whose hash is stored in `password_reset_tokens` for 30 minutes; a new request invalidates earlier links. `POST /auth/reset-password` burns the token, sets the new hash, and revokes every refresh token for the user. Rate limited per IP (429) and per address (silently dropped, still 202).
- **Mail** goes through `services/mailer.py`: `console` logs the message, `smtp` uses `smtplib` in a thread with STARTTLS, SSL, or plain. Tests patch `app.routes.auth.send_email` and read the link out of the captured text (`outbox` fixture).
- The frontend has one deep link, `/reset-password?token=…`, handled by a path check in `App.tsx` rather than a router. nginx's SPA fallback and the Vite dev server both serve `index.html` for it.

## Rate limits

`ratelimit.RateLimiter` keeps a deque of timestamps per key in memory. One uvicorn process means one set of counters; with several workers or instances each allows the full limit on its own. Move counters to Redis before scaling out.

The LLM limiter declares the request body as a dependency so FastAPI validates the body first: a malformed request gets its 422 without consuming a slot. Keep that pattern if you add limiters elsewhere.

Per-IP limits use the socket address unless `TRUST_PROXY_HEADERS=true`, in which case the first `X-Forwarded-For` entry is used. Only enable that behind a proxy you control.

## Logging

`LOG_FORMAT=text` for a terminal, `json` for production. Records can carry `user_id`, `lang`, `duration_s`, `audio_bytes`, `finals`, `event`. Every dictation session logs `stream open` and `stream closed` with its stats. Startup logs the effective model, origins, and caps so a wrong env value is visible immediately.

## Working on this machine without a microphone

- `getUserMedia` returns `NotFoundError` on a PC with no input device. Use `dev-browser.cmd [wav]` to launch Chrome with a WAV file as the microphone. It uses a separate profile under `%TEMP%\chrome-fake-mic`; close that window before relaunching with a different file.
- `test_audio.wav` is English. `test_audio_hinglish.wav` was made with `edge-tts` voice `hi-IN-SwaraNeural` because Windows has no Hindi voice.
- `test_client.py [wav] [lang]` streams a WAV through the backend with the same handshake as the browser. It signs in as `VN_EMAIL` / `VN_PASSWORD` (defaults built in) and creates the account on first use.

## Tests

```bash
cd voice-notes-backend
python -m pytest            # 42 tests, ~8 s
python -m pytest -k dictate # one area
```

- `tests/conftest.py` sets env vars before importing the app, so the real `.env` is never read. The database is a temporary SQLite file; `db.py` switches to `NullPool` for SQLite. Tables come from `Base.metadata` directly, not Alembic, so the startup schema check passes.
- Groq is replaced by patching `summarize_transcript` in the routes (or the Groq client in `test_summarize.py`).
- Deepgram is replaced by `FakeDeepgramConnection`: every chunk yields a partial, `finish()` yields one final, and `start_ok` / `send_ok` flip the failure paths.
- WebSocket tests use Starlette's sync `TestClient`. When patching a limiter for the dictate route, patch `app.routes.dictate.stream_limiter`; the route imported the name directly.

## Migrations

The schema is owned by Alembic. The app checks at startup that the tables exist and refuses to start otherwise, so a missed migration is one obvious error rather than a stream of 500s.

```bash
cd voice-notes-backend
alembic upgrade head                                   # apply
alembic current                                        # what the DB is at
alembic check                                          # models vs DB: should say "No new upgrade operations"
alembic revision --autogenerate -m "add notes.pinned"  # after changing models.py
alembic downgrade -1                                   # undo the last one
```

Rules of thumb:

- Change `models.py` first, then autogenerate, then **read the generated file**. Autogenerate misses renames (it sees drop + add) and does not detect JSON column changes well.
- Every revision needs a working `downgrade`. CI runs upgrade, downgrade, upgrade on SQLite.
- The first revision uses `IF NOT EXISTS` because the Neon database predates Alembic. Later revisions should not need that.
- Revision 0002 adds the notes foreign key inside `batch_alter_table`, which SQLite needs (it rebuilds the table) and Postgres tolerates (plain ALTER).
- The Docker entrypoint runs `alembic upgrade head` before uvicorn, so deploying a new image migrates the database. With one backend instance that is safe; with several, run the migration as a separate release step instead.

## Docker and CI

- `voice-notes-backend/Dockerfile`: python:3.12-slim, non-root user, healthcheck on `/health`, `LOG_FORMAT=json` by default. `docker-entrypoint.sh` migrates, then starts uvicorn on `$PORT` with `$WEB_CONCURRENCY` workers; it adds `--proxy-headers` only when `TRUST_PROXY_HEADERS=true`.
- `voice-notes-frontend/Dockerfile`: two-stage, node build then nginx with SPA fallback and long-cache headers for hashed assets. `VITE_API_URL` is a build arg because Vite inlines it.
- `docker-compose.yml` wires the two together on :8000 and :8080 and injects the frontend origin into `CORS_ORIGINS`.
- `.github/workflows/ci.yml` runs the backend tests, a migration round-trip, frontend lint and build, then builds both images. No secrets needed.
- `.gitattributes` forces LF on `*.sh` so the entrypoint survives a Windows checkout.

## Operational notes

- **Groq retires models without notice.** `llama-3.1-8b-instant` went 404 mid-project. The model name is `GROQ_MODEL` in `.env`.
- **Neon URLs** come as `postgresql://...?sslmode=require&channel_binding=require`. `db.py` swaps in the asyncpg driver and moves SSL into connect args; paste the URL as given.
- **Summaries can be missing.** If Groq fails during save, the note is stored with no title or summary rather than losing the transcript. The frontend shows "(untitled)".

## Roadmap

Done: authentication, rate limits and caps, logging and failure handling, test suite, Docker images, migrations, CI, frontend styling with light and dark themes.

Remaining before a public launch:

1. Redis-backed rate limits when running more than one instance.
2. Frontend unit tests (vitest) for `useDictation` and `auth.ts`.
3. Change-password and delete-account from inside the app.

### Checking the UI without a browser session

Headless Chrome can drive the dev server over the DevTools protocol: start the backend and `npm run dev`, create a user and a couple of notes through the API, then set `localStorage["voice-notes-token"]` via `Runtime.evaluate` and call `Page.captureScreenshot`. `Emulation.setEmulatedMedia` toggles `prefers-color-scheme`; `Emulation.setDeviceMetricsOverride` checks the 390px layout for horizontal overflow.
