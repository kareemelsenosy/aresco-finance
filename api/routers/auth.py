"""
/auth — ARESCO staff sign-in.

First time for an address:
    POST /auth/request-code   {email}                  -> six-digit code by email
    POST /auth/verify-code    {email, code}            -> setup_token
    POST /auth/set-password   {setup_token, password}  -> signed in

Afterwards:
    POST /auth/login          {email, password}        -> signed in
    POST /auth/logout
    GET  /auth/me

Only @aresco.com.eg addresses are accepted; anything else is refused at
request-code and at login.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from api import auth as A
from api import models
from api.config import settings
from api.database import get_db
from api.mailer import send_verification_code, smtp_configured

router = APIRouter(prefix="/auth", tags=["Auth"])


class EmailIn(BaseModel):
    email: str


class VerifyIn(BaseModel):
    email: str
    code: str


class SetPasswordIn(BaseModel):
    setup_token: str
    password: str
    full_name: str = ""


class LoginIn(BaseModel):
    email: str
    password: str


def _issue_session(response: Response, user: models.User) -> None:
    token = A.make_session_token(user.id, user.email)
    response.set_cookie(
        A.SESSION_COOKIE,
        token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
        path="/",
    )


def _user_payload(user: models.User) -> dict:
    return {
        "email": user.email,
        "name": user.display_name,
        "initials": user.initials,
        "is_admin": bool(user.is_admin),
    }


@router.get("/config")
def auth_config():
    """What the login page needs to render before anyone is signed in."""
    return {
        "app_name": settings.app_display_name,
        "allowed_domain": settings.allowed_email_domain,
        "email_delivery": "smtp" if smtp_configured() else "server-log",
    }


@router.post("/request-code")
def request_code(data: EmailIn, db: Session = Depends(get_db)):
    email = A.require_allowed_email(data.email)  # 403 for any other domain

    user = db.query(models.User).filter(models.User.email == email).first()
    if user and user.password_hash:
        raise HTTPException(
            400, "This address already has a password. Sign in instead."
        )
    if user and not user.is_active:
        raise HTTPException(403, "This account has been disabled.")

    recent = (
        db.query(models.EmailVerification)
        .filter(
            models.EmailVerification.email == email,
            models.EmailVerification.created_at > A.utcnow() - timedelta(hours=1),
        )
        .count()
    )
    if recent >= A.MAX_CODES_PER_HOUR:
        raise HTTPException(429, "Too many codes requested. Try again in an hour.")

    if not user:
        user = models.User(email=email)
        db.add(user)
        db.flush()

    # Any earlier live code for this address stops working.
    db.query(models.EmailVerification).filter(
        models.EmailVerification.email == email,
        models.EmailVerification.consumed_at.is_(None),
    ).update({"consumed_at": A.utcnow()})

    code = A.generate_code()
    db.add(
        models.EmailVerification(
            email=email,
            code_hash=A.hash_code(code),
            expires_at=A.utcnow() + timedelta(minutes=settings.code_ttl_minutes),
        )
    )
    db.commit()

    delivered = send_verification_code(email, code)
    return {
        "sent": True,
        "delivered_by": "email" if delivered else "server-log",
        "expires_in_minutes": settings.code_ttl_minutes,
        "message": (
            f"Code sent to {email}."
            if delivered
            else "Mail is not configured on this server — ask whoever runs it to "
                 "read your code from the server log."
        ),
    }


@router.post("/verify-code")
def verify_code(data: VerifyIn, db: Session = Depends(get_db)):
    email = A.require_allowed_email(data.email)

    record = (
        db.query(models.EmailVerification)
        .filter(
            models.EmailVerification.email == email,
            models.EmailVerification.consumed_at.is_(None),
        )
        .order_by(models.EmailVerification.id.desc())
        .first()
    )
    if not record:
        raise HTTPException(400, "No code outstanding for this address. Request one.")
    if record.expires_at < A.utcnow():
        raise HTTPException(400, "That code has expired. Request a new one.")
    if record.attempts >= 5:
        record.consumed_at = A.utcnow()
        db.commit()
        raise HTTPException(429, "Too many wrong attempts. Request a new code.")

    if not A.code_matches((data.code or "").strip(), record.code_hash):
        record.attempts += 1
        db.commit()
        left = 5 - record.attempts
        raise HTTPException(400, f"Incorrect code. {left} attempt(s) left.")

    record.consumed_at = A.utcnow()
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(400, "No pending registration for this address.")
    user.email_verified_at = A.utcnow()
    db.commit()

    return {"verified": True, "setup_token": A.make_setup_token(user.id)}


@router.post("/set-password")
def set_password(data: SetPasswordIn, response: Response, db: Session = Depends(get_db)):
    user_id = A.read_setup_token(data.setup_token)
    if user_id is None:
        raise HTTPException(400, "This setup link has expired. Start again.")

    problem = A.password_problem(data.password or "")
    if problem:
        raise HTTPException(400, problem)

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(400, "Account not found.")
    if user.password_hash:
        raise HTTPException(400, "A password is already set. Sign in instead.")

    user.password_hash = A.hash_password(data.password)
    if data.full_name.strip():
        user.full_name = data.full_name.strip()[:200]
    user.last_login_at = A.utcnow()
    db.commit()

    _issue_session(response, user)
    return {"ok": True, "user": _user_payload(user)}


@router.post("/login")
def login(data: LoginIn, response: Response, db: Session = Depends(get_db)):
    email = A.require_allowed_email(data.email)
    user = db.query(models.User).filter(models.User.email == email).first()

    if user and user.locked_until and user.locked_until > A.utcnow():
        raise HTTPException(
            429, "Too many failed attempts. Try again in a few minutes."
        )

    # Same message whether the address is unknown or the password is wrong, so
    # this endpoint cannot be used to enumerate who has an account.
    if not user or not user.password_hash or not A.verify_password(
        data.password or "", user.password_hash
    ):
        if user:
            user.failed_logins = (user.failed_logins or 0) + 1
            if user.failed_logins >= A.MAX_FAILED_LOGINS:
                user.locked_until = A.utcnow() + timedelta(minutes=A.LOCKOUT_MINUTES)
                user.failed_logins = 0
            db.commit()
        raise HTTPException(401, "Incorrect email or password.")

    if not user.is_active:
        raise HTTPException(403, "This account has been disabled.")

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = A.utcnow()
    db.commit()

    _issue_session(response, user)
    return {"ok": True, "user": _user_payload(user)}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(A.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: models.User = Depends(A.current_user)):
    return _user_payload(user)
