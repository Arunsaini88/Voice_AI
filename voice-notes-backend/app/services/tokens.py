# app/services/tokens.py — refresh-token issue, rotate, revoke.
#
# The raw token is a random 64-char URL-safe string handed to the client once.
# Only its SHA-256 is stored. Every successful refresh issues a new token and
# revokes the old one (rotation). Presenting an already-revoked token means it
# was copied and replayed, so the whole family is revoked (reuse detection).
import hashlib, logging, secrets, uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models import RefreshToken

log = logging.getLogger("app.tokens")


class RefreshError(Exception):
    """Token unknown, expired, revoked, or replayed. Message is safe to show."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres keeps the zone.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def issue_refresh_token(db: AsyncSession, user_id: str, family_id: str | None = None) -> str:
    """Create a refresh token for the user and return the raw value. A new
    family starts at login; rotation passes the existing family through."""
    raw = secrets.token_urlsafe(48)
    db.add(RefreshToken(
        user_id=user_id,
        token_hash=_hash(raw),
        family_id=family_id or str(uuid.uuid4()),
        expires_at=_now() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
    ))
    await db.flush()
    return raw


async def rotate_refresh_token(db: AsyncSession, raw: str) -> tuple[str, str]:
    """Exchange a refresh token for a new one. Returns (user_id, new_raw).
    Raises RefreshError, after revoking the family if the token was replayed."""
    row = await db.scalar(select(RefreshToken).where(RefreshToken.token_hash == _hash(raw)))
    if row is None:
        raise RefreshError("Invalid refresh token")

    if row.revoked_at is not None:
        # Already rotated or logged out, yet presented again: assume theft.
        await revoke_family(db, row.family_id)
        log.warning("refresh token reuse detected; family revoked",
                    extra={"user_id": row.user_id, "event": "refresh_reuse"})
        raise RefreshError("Session is no longer valid. Please log in again.")

    if _aware(row.expires_at) <= _now():
        raise RefreshError("Session expired. Please log in again.")

    row.revoked_at = _now()
    new_raw = await issue_refresh_token(db, row.user_id, row.family_id)
    return row.user_id, new_raw


async def revoke_refresh_token(db: AsyncSession, raw: str) -> None:
    """Logout. Idempotent: unknown or already-revoked tokens are a no-op."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == _hash(raw), RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def revoke_family(db: AsyncSession, family_id: str) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )


async def revoke_all_for_user(db: AsyncSession, user_id: str) -> None:
    """Log out everywhere. Used by password reset."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
