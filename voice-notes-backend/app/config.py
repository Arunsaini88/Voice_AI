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
    # Lifetime of a login token. Long because there is no refresh flow yet.
    JWT_EXPIRE_DAYS: int = 7

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
    RATE_LLM_PER_MIN: int = 20              # summarize + note creation per user
    RATE_STREAMS_PER_MIN: int = 10          # new dictation sessions per user

    class Config:
        env_file = ".env"

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

settings = Settings()
