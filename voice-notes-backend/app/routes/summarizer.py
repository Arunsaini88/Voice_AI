# app/routes/summarizer.py
from fastapi import APIRouter
from pydantic import BaseModel
from app.services.summarize import summarize_transcript

router = APIRouter()

class SummarizeRequest(BaseModel):
    transcript: str
    language: str = "en"

@router.post("/llm/summarize")
async def summarize(payload: SummarizeRequest):
    # Stateless preview. POST /notes does the same and persists the result.
    return await summarize_transcript(payload.transcript, payload.language)
