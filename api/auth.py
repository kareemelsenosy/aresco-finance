"""
Authentication — ARESCO staff only.

Sign-up is by @aresco.com.eg address. A first-time address gets a six-digit code
by email, verifies it, then sets a password. After that it is email + password.

Deliberately standard-library only: PBKDF2-HMAC-SHA256 for passwords and an
HMAC-signed session cookie. The alternative was passlib/bcrypt + python-jose,
which is two more dependencies to pin and patch for no security gain at this
scale.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api.config import settings
from api.database import get_db

# --- password hashing ------------------------------------------------------

_PBKDF2_ROUNDS = 600_000  # OWASP 2023 guidance for PBKDF2-HMAC-SHA256


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
        )
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(dk.hex(), hash_hex)


def password_problem(password: str) -> str | None:
    """Return why the password is unacceptable, or None if it is fine."""
    if len(password) < 10:
        return "Password must be at least 10 characters."
    if password.isdigit() or password.isalpha():
        return "Password must mix letters with numbers or symbols."
    if password.lower() in {"password12", "aresco1234", "1234567890"}:
        return "That password is too easily guessed."
    return None


# --- verification codes ----------------------------------------------------


def generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_code(code: str) -> str:
    """Codes are short, so salt with the app secret rather than storing plaintext."""
    return hmac.new(
        settings.secret_key.encode(), code.encode(), hashlib.sha256
    ).hexdigest()


def code_matches(code: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_code(code), stored_hash)


# --- signed tokens (session cookie and the short-lived setup token) ---------

SESSION_COOKIE = "aresco_session"


def _sign(payload: dict, ttl_seconds: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    b64 = base64.urlsafe_b64encode(raw).rstrip(b"=")
    sig = hmac.new(settings.secret_key.encode(), b64, hashlib.sha256).digest()
    return f"{b64.decode()}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"


def _unsign(token: str) -> dict | None:
    try:
        b64, sig_b64 = token.split(".", 1)
    except (ValueError, AttributeError):
        return None
    expected = hmac.new(settings.secret_key.encode(), b64.encode(), hashlib.sha256).digest()
    try:
        given = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    except Exception:
        return None
    if not hmac.compare_digest(expected, given):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def make_session_token(user_id: int, email: str) -> str:
    return _sign({"uid": user_id, "email": email, "kind": "session"},
                 settings.session_hours * 3600)


def make_setup_token(user_id: int) -> str:
    """Proves 'this person just passed the emailed code', good for 20 minutes."""
    return _sign({"uid": user_id, "kind": "setup"}, 20 * 60)


def read_setup_token(token: str) -> int | None:
    payload = _unsign(token)
    if not payload or payload.get("kind") != "setup":
        return None
    return payload.get("uid")


# --- email domain rule -----------------------------------------------------


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def email_domain_ok(email: str) -> bool:
    email = normalise_email(email)
    if email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    if not local:
        return False
    allowed = settings.allowed_email_domain.strip().lower()
    return domain == allowed


def require_allowed_email(email: str) -> str:
    email = normalise_email(email)
    if not email_domain_ok(email):
        raise HTTPException(
            403,
            f"Access is limited to @{settings.allowed_email_domain} addresses. "
            f"Sign in with your ARESCO email.",
        )
    return email


# --- request dependencies --------------------------------------------------


def current_user_optional(request: Request, db: Session = Depends(get_db)):
    from api import models

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = _unsign(token)
    if not payload or payload.get("kind") != "session":
        return None
    user = db.query(models.User).filter(models.User.id == payload["uid"]).first()
    if not user or not user.is_active or not user.password_hash:
        return None
    return user


def current_user(user=Depends(current_user_optional)):
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return user


# --- throttling ------------------------------------------------------------

MAX_CODES_PER_HOUR = 5
MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def within_last(ts: datetime | None, minutes: int) -> bool:
    return ts is not None and ts > utcnow() - timedelta(minutes=minutes)
