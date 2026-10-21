# app/models.py
from sqlalchemy import Column, String, Text, DateTime, JSON
from sqlalchemy.orm import declarative_base
import uuid, datetime

Base = declarative_base()

def _now():
    return datetime.datetime.now(datetime.timezone.utc)

def _uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=_uuid)
    email = Column(String, nullable=False, unique=True, index=True)   # stored lowercased
    password_hash = Column(String, nullable=False)                    # bcrypt
    created_at = Column(DateTime(timezone=True), default=_now)


class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=_uuid)
    # No FK to users.id: notes created under the old static dev token have
    # user_id "dev-user-1" with no matching row. Add the FK with the first
    # Alembic migration once that data is cleaned up.
    user_id = Column(String, nullable=False, index=True)
    raw_transcript = Column(Text, nullable=False)
    title = Column(String)
    summary = Column(Text)
    key_points = Column(JSON)
    language = Column(String)     # "en" / "hi" / "hinglish" — store what was selected
    created_at = Column(DateTime(timezone=True), default=_now)
