"""
Outbound email for verification codes.

Configured through SMTP_* in .env. When SMTP is not configured the code is
written to the server log instead of being sent, so the flow is still testable
on a machine with no mail relay — see `send_verification_code`.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from api.config import settings

log = logging.getLogger("aresco.mail")


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def _build(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(body)
    return msg


def _send(msg: EmailMessage) -> None:
    if settings.smtp_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(),
            timeout=20,
        ) as s:
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password)
            s.send_message(msg)
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        s.ehlo()
        if settings.smtp_starttls:
            s.starttls(context=ssl.create_default_context())
            s.ehlo()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def send_verification_code(to: str, code: str) -> bool:
    """
    Deliver a sign-up code. Returns True if it actually went out over SMTP.

    With no SMTP configured the code goes to the server log at WARNING so that
    whoever is running the server can read it out. That is a deployment
    convenience for the local server, not a way to run this on the internet.
    """
    subject = f"{settings.app_display_name} — your verification code"
    body = (
        f"Your {settings.app_display_name} verification code is:\n\n"
        f"    {code}\n\n"
        f"It expires in {settings.code_ttl_minutes} minutes.\n\n"
        "If you did not request this, you can ignore this message — someone "
        "typed your address by mistake and cannot get in without the code.\n"
    )

    if not smtp_configured():
        log.warning(
            "SMTP not configured — verification code for %s is %s (expires in %d min)",
            to, code, settings.code_ttl_minutes,
        )
        return False

    try:
        _send(_build(to, subject, body))
        log.info("verification code sent to %s", to)
        return True
    except Exception as exc:
        # Never surface the SMTP error to the caller; it leaks relay details.
        log.error("failed to send verification code to %s: %s: %s",
                  to, type(exc).__name__, exc)
        log.warning("code for %s is %s (delivery failed, read it from here)", to, code)
        return False
