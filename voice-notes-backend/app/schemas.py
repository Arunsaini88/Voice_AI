# app/schemas.py — request shapes shared by the notes and summarize routes.
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from app.config import settings

Language = Literal["en", "hi", "hinglish"]


class TranscriptPayload(BaseModel):
    transcript: str = Field(min_length=1, max_length=settings.MAX_TRANSCRIPT_CHARS)
    language: Language = "en"

    @field_validator("transcript", mode="before")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v
