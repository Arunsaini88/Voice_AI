# app/main.py
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.logging_config import setup_logging

# Configure logging before anything else imports a logger.
setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
log = logging.getLogger("app")

from app.db import schema_ready                             # noqa: E402
from app.routes import auth, dictate, notes, summarizer     # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not await schema_ready():
        raise RuntimeError(
            "Database schema is missing. Run `alembic upgrade head` in voice-notes-backend "
            "(the Docker image does this automatically on start)."
        )
    log.info(
        "startup: groq_model=%s cors_origins=%s max_dictation_s=%d llm_per_min=%d auth_per_min=%d",
        settings.GROQ_MODEL, settings.cors_origins, settings.MAX_DICTATION_SECONDS,
        settings.RATE_LLM_PER_MIN, settings.RATE_AUTH_PER_MIN,
    )
    yield
    log.info("shutdown")


app = FastAPI(title="Voice Notes", lifespan=lifespan)
# Origins come from CORS_ORIGINS in .env (comma-separated). Auth is a bearer
# header, not a cookie, so allow_credentials stays off.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)
app.include_router(auth.router)
app.include_router(dictate.router)
app.include_router(summarizer.router)
app.include_router(notes.router)


@app.get("/health")
def health():
    return {"status": "ok"}
