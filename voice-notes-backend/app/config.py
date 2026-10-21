# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DEEPGRAM_API_KEY: str
    GROQ_API_KEY: str
    DATABASE_URL: str
    JWT_SECRET: str
    # Groq retires models without notice (llama-3.1-8b-instant went 404);
    # keep the name here so a swap is a .env change, not a code hunt.
    GROQ_MODEL: str = "qwen/qwen3.8-27b"
    # Access tokens are short-lived JWTs (not revocable); refresh tokens are
    # stored server-side and rotated on every use, so they can be long-lived.
    ACCESS_TOKEN_MINUTES: int = 30
    REFRESH_TOKEN_DAYS: int = 30
    RESET_TOKEN_MINUTES: int = 30

    # Email. "console" logs each message instead of sending (dev, tests);
    # "smtp" delivers through SMTP_HOST using the credentials below.
    EMAIL_BACKEND: str = "console"
    EMAIL_FROM: str = "Voice Notes <noreply@example.com>"
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_SECURITY: str = "starttls"         # starttls (587) | ssl (465) | none (local relay)
    # Where password-reset links point: the deployed frontend origin in prod.
    FRONTEND_URL: str = "http://localhost:5173"

    # Logging. LOG_FORMAT=json for production log shippers, text for a terminal.
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"

    # Comma-separated browser origins allowed to call the API. Default is the
    # Vite dev server; set to the real frontend URL in production.
    CORS_ORIGINS: str = "http://localhost:5173"
    # Only enable behind a reverse proxy you control; otherwise clients can
    # spoof X-Forwarded-For and dodge the per-IP limits.
    TRUST_PROXY_HEADERS: bool = False

    # Abuse / cost caps. Every LLM and transcription call costs money.
    MAX_TRANSCRIPT_CHARS: int = 20_000      # POST /notes and /llm/summarize
    MAX_DICTATION_SECONDS: int = 600        # one WebSocket session
    MAX_STREAMS_PER_USER: int = 2           # concurrent dictation sessions
    RATE_AUTH_PER_MIN: int = 10             # login/signup attempts per IP
    RATE_RESET_PER_MIN: int = 3             # password-reset emails per IP and per address
    RATE_LLM_PER_MIN: int = 20              # summarize + note creation per user
    RATE_STREAMS_PER_MIN: int = 10          # new dictation sessions per user

    class Config:
        env_file = ".env"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

settings = Settings()
