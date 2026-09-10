# app/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DEEPGRAM_API_KEY: str
    GROQ_API_KEY: str
    DATABASE_URL: str
    JWT_SECRET: str

    class Config:
        env_file = ".env"

settings = Settings()