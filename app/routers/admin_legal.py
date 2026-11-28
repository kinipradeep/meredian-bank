"""Admin: policies (operator details, retention, re-acceptance), retention purge, and deleting a user's data."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import legal
from ..auth import require_admin
from ..db import get_db
from ..models import User
from ..security import verify_csrf
from .admin import audit, back, flash, render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])
TEXT_FIELDS = ("operator_name", "operator_address", "contact_email", "grievance_name", "grievance_email",
               "jurisdiction", "data_location")


@router.get("/policies")
def policies(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    cfg = legal.config(db)
    people = db.query(User).all()
    learners = [u for u in people if not u.is_test_account]
    accepted = sum(1 for u in learners if u.policies_version == cfg["version"])
    return render(request, "policies.html", user, cfg=cfg, facts=legal.facts(db), pages=legal.PAGES,
                  total=len(learners), accepted=accepted)


@router.post("/policies")
def policies_save(request: Request, csrf: str = Form(""), operator_name: str = Form(""), operator_address: str = Form(""),
                  contact_email: str = Form(""), grievance_name: str = Form(""), grievance_email: str = Form(""),
                  jurisdiction: str = Form("India"), data_location: str = Form(""), retention_days: str = Form("180"),
                  minimum_age: str = Form("18"), require_acceptance: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    values = {k: v.strip()[:300] for k, v in zip(TEXT_FIELDS, (operator_name, operator_address, contact_email,
                                                               grievance_name, grievance_email, jurisdiction, data_location))}
    try:
        values["retention_days"] = max(0, min(3650, int(retention_days or 0)))
        values["minimum_age"] = max(13, min(99, int(minimum_age or 18)))
    except ValueError:
        flash(request, "Retention and minimum age must be whole numbers.", "error")
        return back("/admin/policies")
    values["require_acceptance"] = bool(require_acceptance)
    legal.save_config(db, values)
    audit(db, user, "Updated the range policies' settings", **{k: v for k, v in values.items() if k != "operator_address"})
    flash(request, "Saved. The policy pages now show these details.")
    return back("/admin/policies")


@router.post("/policies/reaccept")
def policies_reaccept(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                      user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    t = datetime.now(timezone.utc)
    cfg = legal.save_config(db, {"version": t.strftime("%Y-%m-%d.%H%M%S"), "effective": f"{t.day} {t:%B %Y}"})
    audit(db, user, f"Published policies version {cfg['version']}: everyone must accept again")
    flash(request, f"Version {cfg['version']} published. Everyone, you included, accepts it at their next page load.")
    return back("/admin/policies")


@router.post("/policies/purge")
def policies_purge(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    out = legal.purge(db)
    flash(request, "Nothing to purge: retention is set to keep everything." if "skipped" in out else
          "Purged: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in out.items()) + ".")
    return back("/admin/policies")


@router.post("/users/{uid}/delete")
def user_delete(uid: int, request: Request, csrf: str = Form(""), confirm: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    other = db.get(User, uid)
    if not other:
        flash(request, "No such user.", "error")
    elif other.id == user.id:
        flash(request, "You can't delete your own account here.", "error")
    elif other.is_admin and db.query(User).filter(User.role == "admin", User.is_active.is_(True)).count() <= 1:
        flash(request, "You can't delete the last administrator.", "error")
    elif confirm.strip().lower() != other.email.lower():
        flash(request, "Type the user's email address exactly to confirm the deletion.", "error")
    else:
        email = other.email
        counts = legal.delete_user(db, other, by=user)
        flash(request, f"Deleted {email} and their data ({sum(counts.values())} records). Backups drop it within 14 days."
              + (" Their Langfuse traces must be deleted in Langfuse." if legal.facts(db)["langfuse"] else ""))
    return back("/admin/users")
