# Voice Notes

Dictate in English, Hindi, or Hinglish. Get live text as you speak, then a title, summary, and key points saved as a note.

- **Live transcription** over a WebSocket using Deepgram Nova-3, with code-switching for Hinglish.
- **Hinglish romanization**: Devanagari finals are rewritten the way people actually type ("kal milte hain") using Groq.
- **Summaries** in the language you dictated in, stored with the raw transcript.
- **Accounts**: email and password signup, JWT sessions, notes private per user.
- **Abuse controls**: rate limits on every paid call, transcript and session caps, origin-restricted CORS.

```
voice-notes-frontend/   React 19 + Vite + TypeScript single-page app
voice-notes-backend/    FastAPI + SQLAlchemy (async) + Postgres (Neon)
test_client.py          Streams a WAV file through the backend like a browser would
dev-browser.cmd         Opens Chrome with a WAV file as a fake microphone
docker-compose.yml      Local production-like stack
.github/workflows/      CI: tests, migrations, lint, build, Docker images
docs/DEVELOPMENT.md     Internals: protocol, auth, limits, logging, testing
```

## Quick start

Prerequisites: Python 3.12+, Node 22+, a Postgres database (Neon free tier works), a Deepgram API key, a Groq API key.

### Backend

```bash
cd voice-notes-backend
python -m venv .venv            # or: uv venv
.venv/Scripts/activate          # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt # or: uv pip install -r requirements.txt
cp .env.example .env            # then fill in the four required values
alembic upgrade head            # create / update the tables
uvicorn app.main:app --reload --port 8000
```

Required values in `.env`:

| Variable | What it is |
|---|---|
| `DEEPGRAM_API_KEY` | Speech-to-text |
| `GROQ_API_KEY` | Romanization and summaries |
| `DATABASE_URL` | Postgres URL. Neon's `postgresql://...?sslmode=require` form is accepted as-is |
| `JWT_SECRET` | Any long random string. `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

The schema is managed by Alembic; the app refuses to start until `alembic upgrade head` has run. Health check: `GET http://localhost:8000/health`. Interactive API docs: `http://localhost:8000/docs`.

### Frontend

```bash
cd voice-notes-frontend
npm install
npm run dev                     # http://localhost:5173
```

Copy `.env.example` to `.env` if the backend is not on `http://localhost:8000`.

Open the page via `http://localhost`, not a LAN IP: the microphone API only exists in a secure context.

### Docker

```bash
docker compose up --build       # backend on :8000, frontend on :8080
```

Backend secrets are read from `voice-notes-backend/.env`. The backend container runs migrations on every start. The frontend image bakes the API URL in at build time: set `VITE_API_URL` in the environment (or `--build-arg VITE_API_URL=...` for a plain `docker build`).

## Using it

1. Sign up or log in.
2. Pick a language. Hinglish enables Hindi and English code-switching with romanized output.
3. Press **Start**, speak, press **Stop**. The last sentence is flushed, then the note is summarized and saved.
4. Notes list newest first. Delete with the ✕ button.

## Configuration

All settings live in `voice-notes-backend/.env`. Defaults are tuned for a single-instance beta. See `.env.example` for the full list.

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_MODEL` | `qwen/qwen3.8-27b` | Groq retires models without notice; swap here, not in code |
| `JWT_EXPIRE_DAYS` | `7` | Login token lifetime |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated browser origins. Set to the deployed frontend URL |
| `TRUST_PROXY_HEADERS` | `false` | Read `X-Forwarded-For` for per-IP limits. Only behind your own proxy |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `text` | `json` for log shippers |
| `MAX_TRANSCRIPT_CHARS` | `20000` | Cap on text sent for summarization |
| `MAX_DICTATION_SECONDS` | `600` | Cap on one recording session |
| `MAX_STREAMS_PER_USER` | `2` | Concurrent recordings per account |
| `RATE_AUTH_PER_MIN` | `10` | Login and signup attempts per IP |
| `RATE_LLM_PER_MIN` | `20` | Summaries per user |
| `RATE_STREAMS_PER_MIN` | `10` | New recordings per user |

## API

All routes except `/health`, `/auth/signup`, and `/auth/login` need `Authorization: Bearer <token>`.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/auth/signup` | `{email, password}` → `{access_token, user_id}`. Password 8 to 72 bytes |
| `POST` | `/auth/login` | Same shape. Wrong password and unknown email give the same 401 |
| `GET` | `/auth/me` | `{id, email}` |
| `WS` | `/dictate/stream?lang=en\|hi\|hinglish&token=…` | Send audio bytes, then `{"type":"stop"}`. See the protocol in `docs/DEVELOPMENT.md` |
| `POST` | `/notes` | `{transcript, language}` → summarized and saved note |
| `GET` | `/notes` | Caller's notes, newest first |
| `GET` | `/notes/{id}` | 404 for both missing and someone else's |
| `DELETE` | `/notes/{id}` | 204 |
| `POST` | `/llm/summarize` | Stateless preview of the summary |
| `GET` | `/health` | `{"status":"ok"}` |

Rate-limited calls return 429 with a `Retry-After` header.

## Tests

```bash
cd voice-notes-backend
pip install -r requirements-dev.txt     # or: uv pip install -r requirements-dev.txt
python -m pytest
```

42 tests, about 8 seconds, no network or API keys needed. Deepgram and Groq are faked; the database is a temporary SQLite file.

For an end-to-end check against the real services, with the backend running:

```bash
python test_client.py                                  # English WAV
python test_client.py test_audio_hinglish.wav hinglish # Hinglish WAV
```

## Deploying

1. **Database**: any Postgres. Neon's connection string works unchanged.
2. **Backend**: build `voice-notes-backend/Dockerfile`, supply the variables from `.env.production.example`. The entrypoint runs migrations, then uvicorn on `$PORT` (default 8000). Set `TRUST_PROXY_HEADERS=true` only behind a proxy you control. Keep one worker unless rate limits move to Redis.
3. **Frontend**: build `voice-notes-frontend/Dockerfile` with `--build-arg VITE_API_URL=https://<backend host>`, or run `VITE_API_URL=... npm run build` and host `dist/` on any static host. The page must be served over HTTPS for the microphone to work.
4. **CORS**: set the backend's `CORS_ORIGINS` to the frontend's exact origin.

CI runs on every push: backend tests, a migration round-trip, frontend lint and build, and both Docker images.

## Status

Ready for a private beta: hardened, deployable, with a light and dark themed UI. Not yet done: password reset, token refresh, Redis-backed limits for multi-instance. See the roadmap in `docs/DEVELOPMENT.md`.
