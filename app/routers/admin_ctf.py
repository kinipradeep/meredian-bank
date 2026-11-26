"""Admin: CTF event control, challenge editor, submissions, and the CTF scenario records."""
import json

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ctf
from ..auth import require_admin
from ..db import get_db
from ..models import (Agent, Challenge, ChallengeSolve, FaqEntry, FlagSubmission, HintUnlock, LoanOffer, Setting,
                      TreasuryTransfer, User)
from ..security import verify_csrf
from .admin import audit, back, flash, render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@router.get("/ctf")
def ctf_home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    chs = db.query(Challenge).order_by(Challenge.sort_order, Challenge.id).all()
    counts = dict(db.query(ChallengeSolve.challenge_id, func.count()).group_by(ChallengeSolve.challenge_id).all())
    hint_counts = dict(db.query(HintUnlock.challenge_id, func.count()).filter(HintUnlock.hint_index.between(0, 99))
                       .group_by(HintUnlock.challenge_id).all())
    answer_counts = dict(db.query(HintUnlock.challenge_id, func.count()).filter(HintUnlock.hint_index == ctf.ANSWER_INDEX)
                         .group_by(HintUnlock.challenge_id).all())
    users = {u.id: u for u in db.query(User)}
    subs = db.query(FlagSubmission).order_by(FlagSubmission.id.desc()).limit(40).all()
    shared = db.query(FlagSubmission).filter(FlagSubmission.note.like("flag belongs%")).count()
    return render(request, "ctf.html", user, w=ctf.window(db), chs=chs, counts=counts, hint_counts=hint_counts,
                  answer_counts=answer_counts,
                  subs=subs, users=users, shared=shared, board=ctf.scoreboard(db, respect_freeze=False)[:10],
                  name=ctf.display_name, players=db.query(User).filter(User.role != "admin").count())


@router.post("/ctf/event")
def ctf_event(request: Request, name: str = Form(""), timezone: str = Form("Asia/Kolkata"), start: str = Form(""),
              end: str = Form(""), freeze: str = Form(""), scoreboard_public: str = Form(""),
              show_flags_on_solve: str = Form(""), mode: str = Form("labs"), answers: str = Form("always"),
              csrf: str = Form(""), db: Session = Depends(get_db),
              user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    row = db.get(Setting, "ctf") or Setting(key="ctf", value={})
    row.value = {"name": name or ctf.DEFAULT_EVENT["name"], "timezone": timezone or "Asia/Kolkata",
                 "start": start, "end": end, "freeze": freeze, "scoreboard_public": bool(scoreboard_public),
                 "show_flags_on_solve": bool(show_flags_on_solve),
                 "mode": mode if mode in ("labs", "ctf") else "labs",
                 "answers": answers if answers in ("always", "after_steps", "after_completion", "hidden") else "always"}
    db.add(row)
    db.commit()
    audit(db, user, "Labs/CTF settings updated", mode=mode, answers=answers, start=start, end=end, freeze=freeze)
    flash(request, "Event settings saved.")
    return back("/admin/ctf")


@router.post("/ctf/challenges")
async def ctf_challenges(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = await request.form()
    verify_csrf(request, f.get("csrf"))
    errors = []
    for ch in db.query(Challenge):
        p = f"c{ch.id}_"
        if f.get(p + "points") is None:
            continue
        ch.points = int(f.get(p + "points") or ch.points)
        ch.difficulty = f.get(p + "difficulty") or ch.difficulty
        ch.category = (f.get(p + "category") or ch.category).strip()
        ch.enabled = bool(f.get(p + "enabled"))
        try:
            hints = json.loads(f.get(p + "hints") or "[]")
            assert isinstance(hints, list) and all("text" in h for h in hints)
            ch.hints = [{"text": str(h["text"]), "cost": int(h.get("cost", 0))} for h in hints]
        except (ValueError, AssertionError, TypeError):
            errors.append(ch.slug)
    db.commit()
    audit(db, user, "CTF: challenges edited")
    flash(request, "Challenges saved." + (f" Hints not saved for: {', '.join(errors)} (invalid JSON)." if errors else ""),
          "error" if errors else "ok")
    return back("/admin/ctf")


@router.post("/ctf/reset")
def ctf_reset(request: Request, csrf: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    for model in (ChallengeSolve, HintUnlock, FlagSubmission, TreasuryTransfer, LoanOffer):
        db.query(model).delete()
    db.query(FaqEntry).filter(FaqEntry.user_id.isnot(None)).delete()
    db.commit()
    try:
        from ..agents.tools import FAQ_COLLECTION
        from ..rag import store
        store.get_collection(FAQ_COLLECTION).delete(where={"owner": {"$ne": "official"}})
    except Exception:  # noqa: BLE001
        pass
    audit(db, user, "CTF: full reset (solves, hints, submissions, transfers, offers, community FAQ)")
    flash(request, "CTF reset: scores, hints, submissions, transfers, loan offers and learner FAQ entries cleared.")
    return back("/admin/ctf")


@router.get("/ctf/scenarios")
def ctf_scenarios(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    users = {u.id: u for u in db.query(User)}
    agents = {a.id: a for a in db.query(Agent)}
    return render(request, "ctf_scenarios.html", user, users=users, agents=agents,
                  transfers=db.query(TreasuryTransfer).order_by(TreasuryTransfer.id.desc()).limit(50).all(),
                  offers=db.query(LoanOffer).order_by(LoanOffer.id.desc()).limit(50).all(),
                  faq=db.query(FaqEntry).order_by(FaqEntry.id.desc()).limit(80).all())


@router.post("/ctf/faq/{fid}/{action}")
def faq_moderate(fid: int, action: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    from ..agents.tools import FAQ_COLLECTION, index_faq
    from ..rag import store
    verify_csrf(request, csrf)
    e = db.get(FaqEntry, fid)
    if e and action == "approve" and e.status in ("pending", "published"):
        e.status = "approved"
        db.commit()
        index_faq(db, e)
    elif e and action in ("reject", "remove") and e.user_id is not None and e.status != "rejected":
        e.status = "rejected"
        db.commit()
        try:
            store.get_collection(FAQ_COLLECTION).delete(ids=[f"faq{e.id}"])
        except Exception:  # noqa: BLE001
            pass
    audit(db, user, f"FAQ #{fid} {action}")
    flash(request, f"FAQ entry #{fid}: {action}.")
    return back("/admin/ctf/scenarios#faq")
