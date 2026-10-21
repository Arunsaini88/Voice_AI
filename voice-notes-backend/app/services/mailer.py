# app/services/mailer.py — outbound email.
#
# EMAIL_BACKEND=console  logs the message (default: dev and tests)
# EMAIL_BACKEND=smtp     delivers via smtplib, off the event loop
#
# Only transactional mail (password reset) goes through here, so plain
# smtplib is enough; a provider API can be added as a third backend later.
import asyncio, logging, smtplib, ssl
from email.message import EmailMessage
from app.config import settings

log = logging.getLogger("app.mailer")


class MailError(Exception):
    pass


def _build(to: str, subject: str, text: str, html: str | None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.EMAIL_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def _send_smtp_sync(msg: EmailMessage) -> None:
    security = settings.SMTP_SECURITY.lower()
    if security == "ssl":
        client = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20, context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
    with client:
        client.ehlo()
        if security == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if settings.SMTP_USER:
            client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        client.send_message(msg)


async def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Deliver one message. Raises MailError on failure; callers decide
    whether that is fatal (password reset logs it and still answers 202)."""
    backend = settings.EMAIL_BACKEND.lower()
    msg = _build(to, subject, text, html)

    if backend == "console":
        log.info("email (console backend)\nTo: %s\nSubject: %s\n\n%s", to, subject, text)
        return

    if backend == "smtp":
        if not settings.SMTP_HOST:
            raise MailError("EMAIL_BACKEND=smtp but SMTP_HOST is empty")
        try:
            await asyncio.to_thread(_send_smtp_sync, msg)
        except (smtplib.SMTPException, OSError) as e:
            raise MailError(f"SMTP delivery failed: {e}") from e
        log.info("email sent", extra={"event": "email_sent"})
        return

    raise MailError(f"Unknown EMAIL_BACKEND {settings.EMAIL_BACKEND!r}")
