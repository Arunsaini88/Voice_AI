# app/models.py
from sqlalchemy import Column, String, Text, DateTime, JSON, ForeignKey
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


class RefreshToken(Base):
    """One row per issued refresh token. Only the SHA-256 of the token is
    stored, so a database leak does not hand out sessions. `family_id` ties
    together every token descended from one login; a replayed (already
    revoked) token revokes the whole family."""
    __tablename__ = "refresh_tokens"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String, nullable=False, unique=True, index=True)
    family_id = Column(String, nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=_now)
    revoked_at = Column(DateTime(timezone=True))


class Note(Base):
    __tablename__ = "notes"
    id = Column(String, primary_key=True, default=_uuid)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    raw_transcript = Column(Text, nullable=False)
    title = Column(String)
    summary = Column(Text)
    key_points = Column(JSON)
    language = Column(String)     # "en" / "hi" / "hinglish" — store what was selected
    created_at = Column(DateTime(timezone=True), default=_now)
