# app/routes/summarizer.py
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from app.ratelimit import limit_llm_by_user
from app.schemas import TranscriptPayload
from app.services.summarize import summarize_transcript

router = APIRouter()
log = logging.getLogger("app.summarize")


@router.post("/llm/summarize", dependencies=[Depends(limit_llm_by_user)])
async def summarize(payload: TranscriptPayload):
    # Stateless preview. POST /notes does the same and persists the result.
    # Login + per-user rate limit: every call costs Groq credits.
    try:
        return await summarize_transcript(payload.transcript, payload.language)
    except Exception:  # noqa: BLE001
        log.exception("summarize failed")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Summary service unavailable. Try again shortly.")
