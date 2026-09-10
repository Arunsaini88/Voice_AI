# app/models.py
from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base
import uuid, datetime

Base = declarative_base()

class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, nullable=False)
    raw_transcript = Column(Text, nullable=False)
    title = Column(String)
    summary = Column(Text)
    key_points = Column(JSON)
    language = Column(String)     # "en" / "hi" / "hinglish" — store what was selected
    created_at = Column(DateTime, default=datetime.datetime.utcnow)