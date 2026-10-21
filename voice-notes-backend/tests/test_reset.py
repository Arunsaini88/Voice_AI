# tests/test_reset.py — forgot / reset password, and the mailer backends.
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from sqlalchemy import update
from app import ratelimit
from app.config import settings
from app.db import SessionLocal
from app.models import PasswordResetToken
from app.services import mailer

PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "new-password-xyz-1"
GENERIC = "If that email has an account, a reset link is on its way."


def _token_from(outbox) -> str:
    m = re.search(r"/reset-password\?token=([A-Za-z0-9_%.-]+)", outbox[-1]["text"])
    assert m, outbox[-1]["text"]
    return m.group(1)


async def _forgot(client, email):
    return await client.post("/auth/forgot-password", json={"email": email})


async def _reset(client, token, password=NEW_PASSWORD):
    return await client.post("/auth/reset-password", json={"token": token, "password": password})


async def test_unknown_email_gets_same_reply_and_no_mail(client, outbox):
    r = await _forgot(client, "nobody@example.com")
    assert r.status_code == 202 and r.json()["detail"] == GENERIC
    assert outbox == []


async def test_full_reset_flow(client, user, outbox):
    r = await _forgot(client, user.email.upper())
    assert r.status_code == 202 and r.json()["detail"] == GENERIC
    assert len(outbox) == 1
    mail = outbox[0]
    assert mail["to"] == user.email and "Reset your Voice Notes password" == mail["subject"]
    assert settings.FRONTEND_URL in mail["text"] and "reset-password?token=" in mail["html"]

    token = _token_from(outbox)
    assert (await _reset(client, token)).status_code == 204

    # New password works, old one doesn't.
    assert (await client.post("/auth/login", json={"email": user.email, "password": NEW_PASSWORD})).status_code == 200
    assert (await client.post("/auth/login", json={"email": user.email, "password": PASSWORD})).status_code == 401


async def test_reset_logs_out_every_session(client, user, outbox):
    other = (await client.post("/auth/login", json={"email": user.email, "password": PASSWORD})).json()
    await _forgot(client, user.email)
    await _reset(client, _token_from(outbox))
    for refresh in (user.refresh, other["refresh_token"]):
        assert (await client.post("/auth/refresh", json={"refresh_token": refresh})).status_code == 401


async def test_token_is_single_use(client, user, outbox):
    await _forgot(client, user.email)
    token = _token_from(outbox)
    assert (await _reset(client, token)).status_code == 204
    r = await _reset(client, token, "another-password-9")
    assert r.status_code == 400 and "already been used" in r.json()["detail"]


async def test_new_request_invalidates_previous_link(client, user, outbox):
    await _forgot(client, user.email)
    first = _token_from(outbox)
    await _forgot(client, user.email)
    second = _token_from(outbox)
    assert first != second
    assert (await _reset(client, first)).status_code == 400
    assert (await _reset(client, second)).status_code == 204


async def test_expired_and_garbage_tokens(client, user, outbox):
    assert (await _reset(client, "garbage")).status_code == 400
    await _forgot(client, user.email)
    token = _token_from(outbox)
    async with SessionLocal() as db:
        await db.execute(update(PasswordResetToken).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    r = await _reset(client, token)
    assert r.status_code == 400 and "expired" in r.json()["detail"]
    # Password unchanged.
    assert (await client.post("/auth/login", json={"email": user.email, "password": PASSWORD})).status_code == 200


async def test_weak_password_rejected_without_burning_token(client, user, outbox):
    await _forgot(client, user.email)
    token = _token_from(outbox)
    assert (await _reset(client, token, "short")).status_code == 422
    assert (await _reset(client, token)).status_code == 204


async def test_rate_limits(client, user, outbox, monkeypatch):
    lim = ratelimit.RateLimiter(2, 60)
    monkeypatch.setattr(ratelimit, "reset_limiter", lim)
    # Per-address: pre-fill the address's slots (as other clients would have),
    # then a request for it is silently dropped: 202, no mail.
    lim.retry_after("email:" + user.email)
    lim.retry_after("email:" + user.email)
    assert (await _forgot(client, user.email)).status_code == 202
    assert outbox == []
    # Per-IP: second request from this client still goes through...
    assert (await _forgot(client, "someone-else@example.com")).status_code == 202
    # ...and the third is refused outright.
    r = await _forgot(client, "third@example.com")
    assert r.status_code == 429 and "Retry-After" in r.headers


async def test_mail_failure_still_returns_generic_202(client, user, monkeypatch):
    async def boom(*a, **k):
        raise mailer.MailError("smtp down")
    monkeypatch.setattr("app.routes.auth.send_email", boom)
    r = await _forgot(client, user.email)
    assert r.status_code == 202 and r.json()["detail"] == GENERIC


# ------------------------------------------------------------ mailer backends

async def test_console_backend_logs(monkeypatch, caplog):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "console")
    with caplog.at_level("INFO", logger="app.mailer"):
        await mailer.send_email("a@b.com", "Hi", "the link")
    assert "the link" in caplog.text and "a@b.com" in caplog.text


async def test_smtp_backend_starttls_login_and_send(monkeypatch):
    calls = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))
        def __enter__(self): return self
        def __exit__(self, *a): calls.append(("quit",))
        def ehlo(self): calls.append(("ehlo",))
        def starttls(self, context=None): calls.append(("starttls", context is not None))
        def login(self, u, p): calls.append(("login", u, p))
        def send_message(self, msg): calls.append(("send", msg["To"], msg["Subject"], msg["From"]))

    monkeypatch.setattr(mailer.smtplib, "SMTP", FakeSMTP)
    for k, v in dict(EMAIL_BACKEND="smtp", SMTP_HOST="smtp.test", SMTP_PORT=587, SMTP_USER="u",
                     SMTP_PASSWORD="p", SMTP_SECURITY="starttls", EMAIL_FROM="Voice Notes <no@test>").items():
        monkeypatch.setattr(settings, k, v)
    await mailer.send_email("to@test", "Subj", "body", "<b>body</b>")
    assert calls == [("connect", "smtp.test", 587), ("ehlo",), ("starttls", True), ("ehlo",),
                     ("login", "u", "p"), ("send", "to@test", "Subj", "Voice Notes <no@test>"), ("quit",)]


async def test_smtp_backend_wraps_failures(monkeypatch):
    def explode(*a, **k):
        raise mailer.smtplib.SMTPConnectError(421, "nope")
    monkeypatch.setattr(mailer.smtplib, "SMTP", explode)
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    try:
        await mailer.send_email("to@test", "s", "t")
        assert False, "expected MailError"
    except mailer.MailError as e:
        assert "SMTP delivery failed" in str(e)


async def test_smtp_backend_requires_host(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    try:
        await mailer.send_email("to@test", "s", "t")
        assert False, "expected MailError"
    except mailer.MailError as e:
        assert "SMTP_HOST" in str(e)
