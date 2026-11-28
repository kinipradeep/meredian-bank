"""Admin command line, run on the server:

  python -m app.cli create-admin --email you@example.com              # generates a password and prints it
  python -m app.cli create-admin --email you@example.com --password '...'
  python -m app.cli create-admin --email you@example.com --keep-existing   # no change if it already has one
  ADMIN_PASSWORD='...' python -m app.cli create-admin --email you@example.com   # keeps it out of `ps`
  python -m app.cli remove-password --email you@example.com           # Google sign-in only from now on
  python -m app.cli set-agent-model --model claude-haiku              # every agent -> LiteLLM gateway model
  python -m app.cli set-agent-model --model groq-llama-70b --agents aria-v1,refund-v1
  python -m app.cli set-agent-model --model mock-gullible-1 --endpoint mock   # back to the offline model
  python -m app.cli set-agent-model --list                            # show what each agent uses now
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
    c.add_argument("--keep-password", action="store_true",
                   help="never replace an existing password, even if --password/ADMIN_PASSWORD is given")
    r = sub.add_parser("remove-password", help="remove a local password (Google sign-in only)")
    r.add_argument("--email", required=True)
    m = sub.add_parser("set-agent-model", help="point agents at an endpoint + model (default: LiteLLM gateway)")
    m.add_argument("--model", default="", help="gateway model name, e.g. claude-haiku, gemini-flash-lite, groq-llama-70b")
    m.add_argument("--endpoint", default="gateway", help="endpoint slug: gateway (default), mock, ollama-direct")
    m.add_argument("--agents", default="", help="comma-separated agent slugs; blank = all agents")
    m.add_argument("--list", action="store_true", help="only show the current endpoint/model per agent")
    a = ap.parse_args(argv)

    from . import models  # noqa: F401  (registers tables)
    from .db import Base, SessionLocal, engine, migrate
    from .models import AuditLog, User
    from .security import hash_password
    Base.metadata.create_all(bind=engine)
    migrate()
    db = SessionLocal()
    try:
        if a.cmd == "set-agent-model":
            return _set_agent_model(db, a)
        email = a.email.lower().strip()
        user = db.query(User).filter_by(email=email).first()
        if a.cmd == "remove-password":
            if user:
                user.password_hash = ""
                db.commit()
            print(f"Password removed for {email}.")
            return 0
        a.password = a.password or os.environ.get("ADMIN_PASSWORD", "")
        if user and user.password_hash and ((a.keep_existing and not a.password) or a.keep_password):
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


def _set_agent_model(db, a) -> int:
    from .models import Agent, AuditLog, Provider
    q = db.query(Agent).order_by(Agent.sort_order, Agent.slug)
    if a.list:
        for ag in q:
            p = db.get(Provider, ag.provider_id) if ag.provider_id else None
            print(f"{ag.slug:<24} {(p.slug if p else '-'):<14} {ag.model}")
        return 0
    if not a.model:
        print("--model is required (or use --list).", file=sys.stderr)
        return 2
    endpoint = db.query(Provider).filter_by(slug=a.endpoint).first()
    if not endpoint:
        print(f"No endpoint '{a.endpoint}'. Known: {', '.join(p.slug for p in db.query(Provider))}", file=sys.stderr)
        return 2
    wanted = [s.strip() for s in a.agents.split(",") if s.strip()]
    agents = q.filter(Agent.slug.in_(wanted)).all() if wanted else q.all()
    missing = set(wanted) - {ag.slug for ag in agents}
    if missing:
        print(f"Unknown agent(s): {', '.join(sorted(missing))}", file=sys.stderr)
        return 2
    endpoint.enabled = True
    for ag in agents:
        ag.provider_id, ag.model = endpoint.id, a.model
    db.add(AuditLog(event="admin", summary=f"set {len(agents)} agent(s) to {a.endpoint}/{a.model} (cli)"))
    db.commit()
    print(f"{len(agents)} agent(s) now use {a.endpoint} / {a.model}:")
    for ag in agents:
        print(f"  {ag.slug}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
