"""Participant-facing CTF: challenge board, challenge pages, flag submission, hints, scoreboard."""
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import ctf
from ..auth import require_user
from ..db import get_db
from ..models import Agent, Challenge, ChallengeSolve, HintUnlock, User
from ..security import check_rate, verify_csrf
from ..seed.seed import setting
from ..web import templates

router = APIRouter()
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2, "insane": 3}


def _ctx(request, db, user, **extra):
    banner = (setting(db, "banner", {}) or {}).get("text", "")
    return {"user": user, "banner": banner, "w": ctf.window(db), **extra}


def _launch(db, ch) -> str:
    a = db.query(Agent).filter_by(slug=ch.agent_slug).first() if ch.agent_slug else None
    if not a or not a.enabled:
        return ""
    return {"support": f"/support?agent={a.slug}", "refunds": f"/disputes?agent={a.slug}"}.get(a.surface, f"/lab/agent/{a.slug}")


def _visible(db, user) -> bool:
    return user.is_admin or ctf.window(db)["state"] != "not_started"


@router.get("/ctf")
def board(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    chs = db.query(Challenge).filter_by(enabled=True).all() if _visible(db, user) else []
    chs.sort(key=lambda c: (c.sort_order, DIFF_ORDER.get(c.difficulty, 9), c.points))
    mine = {s.challenge_id for s in db.query(ChallengeSolve).filter_by(user_id=user.id)}
    counts = defaultdict(int)
    first = {}
    users = {u.id: u for u in db.query(User)}
    for s in db.query(ChallengeSolve).order_by(ChallengeSolve.solved_at):
        counts[s.challenge_id] += 1
        if s.first_blood and s.user_id in users:
            first[s.challenge_id] = ctf.display_name(users[s.user_id])
    cats = defaultdict(list)
    for c in chs:
        cats[c.category or "General"].append(c)
    board_rows = ctf.scoreboard(db)
    me = next((r for r in board_rows if r["user"].id == user.id), None)
    return templates.TemplateResponse(request, "ctf/board.html", _ctx(
        request, db, user, cats=dict(cats), mine=mine, counts=counts, first=first, total=len(chs),
        score=ctf.player_score(db, user), rank=me["rank"] if me else None, players=len(board_rows),
        flash=request.session.pop("ctf_flash", None)))


@router.get("/ctf/c/{slug}")
def challenge(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    ch = db.query(Challenge).filter_by(slug=slug, enabled=True).first()
    if not ch or not _visible(db, user):
        return RedirectResponse("/ctf", status_code=303)
    solve = db.query(ChallengeSolve).filter_by(user_id=user.id, challenge_id=ch.id).first()
    unlocked = {h.hint_index for h in db.query(HintUnlock).filter_by(user_id=user.id, challenge_id=ch.id)}
    solves = db.query(ChallengeSolve).filter_by(challenge_id=ch.id).order_by(ChallengeSolve.solved_at).all()
    users = {u.id: u for u in db.query(User)}
    ev = ctf.event(db)
    return templates.TemplateResponse(request, "ctf/challenge.html", _ctx(
        request, db, user, ch=ch, solve=solve, unlocked=unlocked, launch=_launch(db, ch),
        solvers=[(ctf.display_name(users[s.user_id]), s) for s in solves[:10] if s.user_id in users],
        nsolves=len(solves), flag=ctf.flag_for(user.id, ch.slug) if solve and ev.get("show_flags_on_solve") else "",
        flash=request.session.pop("ctf_flash", None)))


@router.post("/ctf/submit")
def submit(request: Request, flag: str = Form(...), next: str = Form("/ctf"), csrf: str = Form(""),
           db: Session = Depends(get_db), user: User = Depends(require_user)):
    verify_csrf(request, csrf)
    check_rate(f"flag:{user.id}", per_minute=10)
    w = ctf.window(db)
    if w["state"] != "running":
        res = {"ok": False, "msg": "The CTF is not running right now." if w["state"] == "not_started" else "The CTF has ended."}
    else:
        res = ctf.submit_flag(db, user, flag)
    request.session["ctf_flash"] = {"kind": "ok" if res["ok"] else "error", "msg": res["msg"]}
    return RedirectResponse(next if next.startswith("/ctf") else "/ctf", status_code=303)


@router.post("/ctf/c/{slug}/hint/{index}")
def hint(slug: str, index: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
         user: User = Depends(require_user)):
    verify_csrf(request, csrf)
    ch = db.query(Challenge).filter_by(slug=slug, enabled=True).first()
    if ch:
        ok, msg = ctf.unlock_hint(db, user, ch, index)
        request.session["ctf_flash"] = {"kind": "ok" if ok else "error", "msg": msg}
    return RedirectResponse(f"/ctf/c/{slug}#hints", status_code=303)


@router.get("/ctf/scoreboard")
def scoreboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    w = ctf.window(db)
    if not (w["event"].get("scoreboard_public") or user.is_admin):
        return RedirectResponse("/ctf", status_code=303)
    rows = ctf.scoreboard(db, respect_freeze=not user.is_admin)
    q = db.query(ChallengeSolve).order_by(ChallengeSolve.solved_at.desc())
    if w["frozen"] and not user.is_admin:
        q = q.filter(ChallengeSolve.solved_at < w["freeze"])
    users = {u.id: u for u in db.query(User)}
    chs = {c.id: c for c in db.query(Challenge)}
    feed = [(ctf.display_name(users[s.user_id]), chs[s.challenge_id], s) for s in q.limit(20)
            if s.user_id in users and s.challenge_id in chs and users[s.user_id].role != "admin"]
    return templates.TemplateResponse(request, "ctf/scoreboard.html", _ctx(
        request, db, user, rows=rows, feed=feed, name=ctf.display_name,
        max_points=sum(c.points for c in chs.values() if c.enabled)))
