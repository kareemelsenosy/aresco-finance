"""
Create or update an account directly, skipping the emailed code.

For bootstrapping the first accounts on a new server, or resetting a password
when mail is not configured yet. Everything it does is what the sign-up flow
does — the account is marked email-verified and given a password hash.

    .venv/bin/python scripts/seed_user.py kelsenosy@aresco.com.eg 'secret123!'
    .venv/bin/python scripts/seed_user.py you@aresco.com.eg 'pw' --name "Your Name" --admin
    .venv/bin/python scripts/seed_user.py you@aresco.com.eg 'pw' --force-weak
    .venv/bin/python scripts/seed_user.py --list

The address must be at the allowed domain — the same rule the login page
enforces. Passwords must meet the normal strength rule unless --force-weak is
passed, which exists because a short password on an internal tool is the
operator's call to make, not this script's.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import auth as A  # noqa: E402
from api import models  # noqa: E402
from api.config import settings  # noqa: E402
from api.database import SessionLocal, init_db  # noqa: E402


def list_users(db):
    users = db.query(models.User).order_by(models.User.id).all()
    if not users:
        print("No accounts yet.")
        return
    print(f"{'id':>3}  {'email':<34} {'name':<22} {'pw':<4} {'admin':<6} last login")
    for u in users:
        print(f"{u.id:>3}  {u.email:<34} {(u.full_name or '—'):<22} "
              f"{'yes' if u.password_hash else 'no':<4} "
              f"{'yes' if u.is_admin else 'no':<6} "
              f"{u.last_login_at or '—'}")


def main():
    ap = argparse.ArgumentParser(add_help=True, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("email", nargs="?", help="ARESCO address")
    ap.add_argument("password", nargs="?", help="password to set")
    ap.add_argument("--name", default="", help="display name")
    ap.add_argument("--admin", action="store_true", help="mark as admin")
    ap.add_argument("--force-weak", action="store_true",
                    help="accept a password that fails the strength rule")
    ap.add_argument("--list", action="store_true", help="list existing accounts")
    args = ap.parse_args()

    init_db()
    db = SessionLocal()
    try:
        if args.list:
            list_users(db)
            return
        if not args.email or not args.password:
            ap.error("give an email and a password, or use --list")

        email = A.normalise_email(args.email)
        if not A.email_domain_ok(email):
            print(f"Refused: {email} is not at @{settings.allowed_email_domain}.")
            raise SystemExit(1)

        problem = A.password_problem(args.password)
        if problem and not args.force_weak:
            print(f"Refused: {problem}")
            print("Pass --force-weak to set it anyway.")
            raise SystemExit(1)
        if problem:
            print(f"WARNING: {problem} Setting it anyway (--force-weak).")

        user = db.query(models.User).filter(models.User.email == email).first()
        action = "updated" if user else "created"
        if not user:
            user = models.User(email=email)
            db.add(user)

        user.password_hash = A.hash_password(args.password)
        user.email_verified_at = user.email_verified_at or A.utcnow()
        user.is_active = True
        user.failed_logins = 0
        user.locked_until = None
        if args.name:
            user.full_name = args.name.strip()[:200]
        if args.admin:
            user.is_admin = True
        db.commit()

        print(f"{action}: {email}"
              f"{' (admin)' if user.is_admin else ''}"
              f"{' — ' + user.full_name if user.full_name else ''}")
        print("Sign in at /app/login.html with this address and password.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
