# app/models.py
from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base
import uuid, datetime

Base = declarative_base()

def _now():
    return datetime.datetime.now(datetime.timezone.utc)

class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False, index=True)
    raw_transcript = Column(Text, nullable=False)
    title = Column(String)
    summary = Column(Text)
    key_points = Column(JSON)
    language = Column(String)     # "en" / "hi" / "hinglish" — store what was selected
    created_at = Column(DateTime(timezone=True), default=_now)
