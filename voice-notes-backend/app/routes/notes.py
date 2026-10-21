# app/routes/notes.py
import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.deps import get_current_user
from app.models import Note
from app.ratelimit import limit_llm_by_user
from app.schemas import TranscriptPayload
from app.services.summarize import summarize_transcript

router = APIRouter(prefix="/notes", tags=["notes"])
log = logging.getLogger("app.notes")


class NoteCreate(TranscriptPayload):
    pass


class NoteOut(BaseModel):
    id: str
    title: str | None
    summary: str | None
    key_points: list[str] | None
    language: str | None
    raw_transcript: str
    created_at: datetime

    model_config = {"from_attributes": True}


async def _owned_note(note_id: str, user: dict, db: AsyncSession) -> Note:
    note = await db.get(Note, note_id)
    # Same 404 for "doesn't exist" and "not yours" — don't leak ids across users.
    if note is None or note.user_id != user["id"]:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Note not found")
    return note


@router.post("", response_model=NoteOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_llm_by_user)])
async def create_note(
    payload: NoteCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        s = await summarize_transcript(payload.transcript, payload.language)
    except Exception:  # noqa: BLE001
        # Groq outage must not lose a transcript the user just spent minutes
        # dictating. Save it without a summary; the log carries the cause.
        log.exception("summarize failed; saving note without summary", extra={"user_id": user["id"]})
        s = {"title": None, "summary": None, "key_points": None}
    note = Note(
        user_id=user["id"],
        raw_transcript=payload.transcript,
        language=payload.language,
        title=s.get("title"),
        summary=s.get("summary"),
        key_points=s.get("key_points"),
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return note


@router.get("", response_model=list[NoteOut])
async def list_notes(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(
        select(Note).where(Note.user_id == user["id"]).order_by(Note.created_at.desc())
    )
    return rows.all()


@router.get("/{note_id}", response_model=NoteOut)
async def get_note(note_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _owned_note(note_id, user, db)


@router.delete("/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_note(note_id: str, user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    note = await _owned_note(note_id, user, db)
    await db.delete(note)
    await db.commit()
