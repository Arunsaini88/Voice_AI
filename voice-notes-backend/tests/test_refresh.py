# tests/test_refresh.py — refresh-token rotation, reuse detection, logout.
from datetime import datetime, timedelta, timezone
from jose import jwt
from sqlalchemy import select, update
from app.config import settings
from app.db import SessionLocal
from app.deps import ALGORITHM
from app.models import RefreshToken
from app.services.tokens import revoke_all_for_user


async def _refresh(client, token):
    return await client.post("/auth/refresh", json={"refresh_token": token})


async def test_login_returns_short_access_and_refresh(client, user):
    body = (await client.post("/auth/login", json={"email": user.email, "password": "correct-horse-battery"})).json()
    assert body["expires_in"] == settings.ACCESS_TOKEN_MINUTES * 60
    claims = jwt.decode(body["access_token"], settings.JWT_SECRET, algorithms=[ALGORITHM])
    life = datetime.fromtimestamp(claims["exp"], timezone.utc) - datetime.fromtimestamp(claims["iat"], timezone.utc)
    assert life == timedelta(minutes=settings.ACCESS_TOKEN_MINUTES)
    assert len(body["refresh_token"]) >= 40


async def test_refresh_rotates(client, user):
    r = await _refresh(client, user.refresh)
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == user.id
    assert body["refresh_token"] != user.refresh
    # New access token works and the new refresh token rotates again.
    assert (await client.get("/notes", headers={"Authorization": f"Bearer {body['access_token']}"})).status_code == 200
    assert (await _refresh(client, body["refresh_token"])).status_code == 200
    # The original token is spent. (Presenting it is also a replay, which
    # test_reuse_revokes_whole_family covers.)
    assert (await _refresh(client, user.refresh)).status_code == 401


async def test_reuse_revokes_whole_family(client, user):
    second = (await _refresh(client, user.refresh)).json()["refresh_token"]
    third = (await _refresh(client, second)).json()["refresh_token"]
    # Replay an already-rotated token: theft signal.
    r = await _refresh(client, second)
    assert r.status_code == 401
    assert "log in again" in r.json()["detail"].lower()
    # The latest, otherwise-valid token in the family is dead too.
    assert (await _refresh(client, third)).status_code == 401
    # A fresh login starts a new family that works.
    fresh = (await client.post("/auth/login", json={"email": user.email, "password": "correct-horse-battery"})).json()
    assert (await _refresh(client, fresh["refresh_token"])).status_code == 200


async def test_families_are_independent(client, user):
    other = (await client.post("/auth/login", json={"email": user.email, "password": "correct-horse-battery"})).json()
    await _refresh(client, user.refresh)
    assert (await _refresh(client, user.refresh)).status_code == 401       # replay kills family A
    assert (await _refresh(client, other["refresh_token"])).status_code == 200  # family B unaffected


async def test_unknown_and_expired_tokens(client, user):
    assert (await _refresh(client, "not-a-token")).status_code == 401
    async with SessionLocal() as db:
        await db.execute(update(RefreshToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    r = await _refresh(client, user.refresh)
    assert r.status_code == 401 and "expired" in r.json()["detail"].lower()


async def test_logout_revokes_and_is_idempotent(client, user):
    assert (await client.post("/auth/logout", json={"refresh_token": user.refresh})).status_code == 204
    assert (await client.post("/auth/logout", json={"refresh_token": user.refresh})).status_code == 204
    assert (await client.post("/auth/logout", json={"refresh_token": "nope"})).status_code == 204
    assert (await _refresh(client, user.refresh)).status_code == 401
    # Access token is stateless: still valid until it expires.
    assert (await client.get("/notes", headers=user.headers)).status_code == 200


async def test_revoke_all_for_user(client, user, other_user):
    second = (await client.post("/auth/login", json={"email": user.email, "password": "correct-horse-battery"})).json()
    async with SessionLocal() as db:
        await revoke_all_for_user(db, user.id)
        await db.commit()
    assert (await _refresh(client, user.refresh)).status_code == 401
    assert (await _refresh(client, second["refresh_token"])).status_code == 401
    assert (await _refresh(client, other_user.refresh)).status_code == 200


async def test_only_hashes_are_stored(client, user):
    async with SessionLocal() as db:
        rows = (await db.scalars(select(RefreshToken))).all()
    assert rows and all(r.token_hash != user.refresh and len(r.token_hash) == 64 for r in rows)
