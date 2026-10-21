# app/routes/auth.py
import asyncio, re
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db import get_db
from app.deps import create_access_token, get_current_user
from app.models import User
from app.ratelimit import limit_auth_by_ip
from app.services.tokens import RefreshError, issue_refresh_token, revoke_refresh_token, rotate_refresh_token

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Verified against on login when the email is unknown, so a miss costs the same
# time as a wrong password and the response can't be used to enumerate accounts.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt())


class Credentials(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v) or len(v) > 254:
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def _password(cls, v: str) -> str:
        # bcrypt only looks at the first 72 bytes; refuse longer so nothing is silently ignored.
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if len(v.encode()) > 72:
            raise ValueError("Password must be at most 72 bytes")
        return v


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int          # access token lifetime, seconds
    user_id: str


class RefreshIn(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


async def _session(db: AsyncSession, user_id: str, family_id: str | None = None) -> TokenOut:
    refresh = await issue_refresh_token(db, user_id, family_id)
    await db.commit()
    return TokenOut(
        access_token=create_access_token(user_id),
        refresh_token=refresh,
        expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
        user_id=user_id,
    )


async def _hash(password: str) -> str:
    # ~100-250 ms of CPU; keep it off the event loop.
    h = await asyncio.to_thread(bcrypt.hashpw, password.encode(), bcrypt.gensalt())
    return h.decode()


async def _verify(password: str, hashed: bytes) -> bool:
    return await asyncio.to_thread(bcrypt.checkpw, password.encode(), hashed)


@router.post("/signup", response_model=TokenOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(limit_auth_by_ip)])
async def signup(payload: Credentials, db: AsyncSession = Depends(get_db)):
    user = User(email=payload.email, password_hash=await _hash(payload.password))
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    return await _session(db, user.id)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_auth_by_ip)])
async def login(payload: Credentials, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == payload.email))
    ok = await _verify(payload.password, user.password_hash.encode() if user else _DUMMY_HASH)
    if user is None or not ok:
        raise _unauthorized("Invalid email or password")
    return await _session(db, user.id)


@router.post("/refresh", response_model=TokenOut)
async def refresh(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    """Trade a refresh token for a new access + refresh pair. The old refresh
    token stops working; presenting it again revokes the whole session family."""
    try:
        user_id, new_refresh = await rotate_refresh_token(db, payload.refresh_token)
    except RefreshError as e:
        await db.commit()      # persist a family revocation triggered by reuse
        raise _unauthorized(str(e))
    await db.commit()
    return TokenOut(
        access_token=create_access_token(user_id),
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_MINUTES * 60,
        user_id=user_id,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshIn, db: AsyncSession = Depends(get_db)):
    """Revoke the refresh token. The access token keeps working until it
    expires (at most ACCESS_TOKEN_MINUTES), which is the trade-off for
    stateless access checks."""
    await revoke_refresh_token(db, payload.refresh_token)
    await db.commit()


@router.get("/me", response_model=UserOut)
async def me(current: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    user = await db.get(User, current["id"])
    if user is None:
        # Valid token for a deleted account.
        raise _unauthorized("Account not found")
    return user
