"""Admin command line, run on the server:

  python -m app.cli create-admin --email you@example.com              # generates a password and prints it
  python -m app.cli create-admin --email you@example.com --password '...'
  python -m app.cli create-admin --email you@example.com --keep-existing   # no change if it already has one
  ADMIN_PASSWORD='...' python -m app.cli create-admin --email you@example.com   # keeps it out of `ps`
  python -m app.cli remove-password --email you@example.com           # Google sign-in only from now on
"""
import argparse
import os
import secrets
import string
import sys

MIN_LENGTH = 12


def _generate() -> str:
    alphabet = string.ascii_letters + string.digits
    return "-".join("".join(secrets.choice(alphabet) for _ in range(6)) for _ in range(4))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="create or update a local admin account")
    c.add_argument("--email", required=True)
    c.add_argument("--password", default="")
    c.add_argument("--name", default="")
    c.add_argument("--keep-existing", action="store_true")
    r = sub.add_parser("remove-password", help="remove a local password (Google sign-in only)")
    r.add_argument("--email", required=True)
    a = ap.parse_args(argv)

    from . import models  # noqa: F401  (registers tables)
    from .db import Base, SessionLocal, engine, migrate
    from .models import AuditLog, User
    from .security import hash_password
    Base.metadata.create_all(bind=engine)
    migrate()
    db = SessionLocal()
    try:
        email = a.email.lower().strip()
        user = db.query(User).filter_by(email=email).first()
        if a.cmd == "remove-password":
            if user:
                user.password_hash = ""
                db.commit()
            print(f"Password removed for {email}.")
            return 0
        a.password = a.password or os.environ.get("ADMIN_PASSWORD", "")
        if user and user.password_hash and a.keep_existing and not a.password:
            print(f"Admin {email} already has a password; kept it.")
            return 0
        password = a.password or _generate()
        if len(password) < MIN_LENGTH:
            print(f"Password must be at least {MIN_LENGTH} characters.", file=sys.stderr)
            return 2
        if not user:
            user = User(email=email, name=a.name or email.split("@")[0])
            db.add(user)
        user.role = "admin"
        user.is_active = True
        user.password_hash = hash_password(password)
        if a.name:
            user.name = a.name
        db.commit()
        from .seed.seed import provision_customer
        provision_customer(db, user)
        db.add(AuditLog(user_id=user.id, event="admin", summary=f"local admin account set for {email} (cli)"))
        db.commit()
        print(f"Admin account ready: {email}")
        if not a.password:
            print(f"Password: {password}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
