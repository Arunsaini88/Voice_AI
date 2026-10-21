# tests/test_auth.py
from datetime import datetime, timedelta, timezone
from jose import jwt
from app.config import settings
from app.deps import ALGORITHM, create_access_token, user_from_token

CREDS = {"email": "Carol@Example.com", "password": "correct-horse-battery"}


async def test_signup_returns_token_and_normalizes_email(client):
    r = await client.post("/auth/signup", json=CREDS)
    assert r.status_code == 201
    body = r.json()
    assert body["token_type"] == "bearer"
    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json() == {"id": body["user_id"], "email": "carol@example.com"}


async def test_duplicate_signup_conflicts_case_insensitively(client):
    assert (await client.post("/auth/signup", json=CREDS)).status_code == 201
    r = await client.post("/auth/signup", json={**CREDS, "email": "carol@example.com"})
    assert r.status_code == 409


async def test_signup_validation(client):
    bad = [
        ({"email": "not-an-email", "password": "correct-horse-battery"}, "email"),
        ({"email": "x@y.com", "password": "short"}, "8 characters"),
        ({"email": "x@y.com", "password": "p" * 73}, "72 bytes"),
    ]
    for payload, fragment in bad:
        r = await client.post("/auth/signup", json=payload)
        assert r.status_code == 422, payload
        assert fragment in r.text


async def test_login_success_and_failures(client):
    await client.post("/auth/signup", json=CREDS)
    ok = await client.post("/auth/login", json=CREDS)
    assert ok.status_code == 200 and "access_token" in ok.json()

    wrong = await client.post("/auth/login", json={**CREDS, "password": "wrong-password-1"})
    unknown = await client.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever-123"})
    assert wrong.status_code == unknown.status_code == 401
    # Same message either way: no account enumeration.
    assert wrong.json()["detail"] == unknown.json()["detail"]


async def test_protected_routes_reject_missing_and_bad_tokens(client):
    for headers in ({}, {"Authorization": "Bearer garbage"}, {"Authorization": "Basic abc"}):
        r = await client.get("/notes", headers=headers)
        assert r.status_code == 401, headers
        assert r.headers["WWW-Authenticate"] == "Bearer"


async def test_expired_token_rejected(client, user):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    expired = jwt.encode({"sub": user.id, "exp": past}, settings.JWT_SECRET, algorithm=ALGORITHM)
    r = await client.get("/notes", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_token_roundtrip_and_tampering():
    token = create_access_token("user-123")
    assert user_from_token(token) == {"id": "user-123"}
    assert user_from_token(token[:-2] + "xx") is None
    other_secret = jwt.encode({"sub": "user-123"}, "another-secret", algorithm=ALGORITHM)
    assert user_from_token(other_secret) is None
    no_sub = jwt.encode({"foo": "bar"}, settings.JWT_SECRET, algorithm=ALGORITHM)
    assert user_from_token(no_sub) is None


async def test_me_with_token_for_deleted_user(client, user):
    # A token whose subject no longer exists must not pass as logged in.
    ghost = create_access_token("00000000-0000-0000-0000-000000000000")
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {ghost}"})
    assert r.status_code == 401
