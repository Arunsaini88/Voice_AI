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
    # Static bearer token accepted by get_current_user for local dev. Unset in prod.
    DEV_TOKEN: str | None = None

    class Config:
        env_file = ".env"

settings = Settings()
