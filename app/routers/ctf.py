"""Participant-facing CTF: challenge board, challenge pages, flag submission, hints, scoreboard."""
import re
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


def _lab_ctx(db, user):
    hu = db.query(HintUnlock).filter_by(user_id=user.id).all()
    steps_seen, answered = defaultdict(int), set()
    for h in hu:
        if h.hint_index == ctf.ANSWER_INDEX:
            answered.add(h.challenge_id)
        elif h.hint_index >= ctf.STEP_BASE:
            steps_seen[h.challenge_id] = max(steps_seen[h.challenge_id], h.hint_index - ctf.STEP_BASE + 1)
    return steps_seen, answered


@router.get("/labs")
def labs_board(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return board(request, db, user)


@router.get("/labs/{slug}")
def labs_lab(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return challenge(slug, request, db, user)


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
    if ctf.labs_mode(db):
        modules = defaultdict(list)
        for c in sorted(chs, key=lambda c: (c.module_order, c.sort_order)):
            modules[c.module or "More labs"].append(c)
        steps_seen, answered = _lab_ctx(db, user)
        return templates.TemplateResponse(request, "labs/board.html", _ctx(
            request, db, user, modules=dict(modules), mine=mine, total=len(chs), answered=answered,
            launch={c.id: _launch(db, c) for c in chs}, flash=request.session.pop("ctf_flash", None)))
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
    if ctf.labs_mode(db):
        steps_seen, answered = _lab_ctx(db, user)
        seen = steps_seen.get(ch.id, 0)
        can_answer, why = ctf.answer_allowed(db, user, ch, bool(solve), seen)
        hard = db.query(Agent).filter_by(slug=ch.hardened_slug, enabled=True).first() if ch.hardened_slug else None
        all_labs = sorted(db.query(Challenge).filter_by(enabled=True).all(), key=lambda c: (c.module_order, c.sort_order))
        i = next((k for k, c in enumerate(all_labs) if c.id == ch.id), 0)
        return templates.TemplateResponse(request, "labs/lab.html", _ctx(
            request, db, user, ch=ch, solve=solve, launch=_launch(db, ch), seen=seen,
            answered=ch.id in answered, can_answer=can_answer, why=why,
            hardened=hard and {"support": f"/support?agent={hard.slug}", "refunds": f"/disputes?agent={hard.slug}"}.get(
                hard.surface, f"/lab/agent/{hard.slug}"), hardened_name=hard.name if hard else "",
            flag=ctf.flag_for(user.id, ch.slug) if solve else "",
            prev=all_labs[i - 1] if i > 0 else None, next_lab=all_labs[i + 1] if i + 1 < len(all_labs) else None,
            number=f"{(ch.module or '0.').split('.')[0]}.{(ch.module_order % 100) or 1}",
            flash=request.session.pop("ctf_flash", None)))
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
        if ctf.labs_mode(db) and res["ok"]:
            res["msg"] = re.sub(r" solved for \d+ points\.( First blood! 🩸)?", " completed. Well done!", res["msg"])
    request.session["ctf_flash"] = {"kind": "ok" if res["ok"] else "error", "msg": res["msg"]}
    return RedirectResponse(next if next.startswith(("/ctf", "/labs")) else "/ctf", status_code=303)


@router.post("/ctf/c/{slug}/hint/{index}")
def hint(slug: str, index: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
         user: User = Depends(require_user)):
    verify_csrf(request, csrf)
    ch = db.query(Challenge).filter_by(slug=slug, enabled=True).first()
    if ch:
        ok, msg = ctf.unlock_hint(db, user, ch, index)
        request.session["ctf_flash"] = {"kind": "ok" if ok else "error", "msg": msg}
    return RedirectResponse(f"/ctf/c/{slug}#hints", status_code=303)


@router.post("/labs/{slug}/step")
def lab_step(slug: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
             user: User = Depends(require_user)):
    verify_csrf(request, csrf)
    ch = db.query(Challenge).filter_by(slug=slug, enabled=True).first()
    if ch:
        seen = _lab_ctx(db, user)[0].get(ch.id, 0)
        if seen < len(ch.steps or []):
            ctf.reveal(db, user, ch, ctf.STEP_BASE + seen)
    return RedirectResponse(f"/labs/{slug}#steps", status_code=303)


@router.post("/labs/{slug}/answer")
def lab_answer(slug: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
               user: User = Depends(require_user)):
    verify_csrf(request, csrf)
    ch = db.query(Challenge).filter_by(slug=slug, enabled=True).first()
    if ch:
        solved = bool(db.query(ChallengeSolve).filter_by(user_id=user.id, challenge_id=ch.id).first())
        ok, why = ctf.answer_allowed(db, user, ch, solved, _lab_ctx(db, user)[0].get(ch.id, 0))
        if ok:
            ctf.reveal(db, user, ch, ctf.ANSWER_INDEX)
        else:
            request.session["ctf_flash"] = {"kind": "error", "msg": why}
    return RedirectResponse(f"/labs/{slug}#answer", status_code=303)


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
