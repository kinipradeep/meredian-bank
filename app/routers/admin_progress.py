"""Admin: per-learner lab progress (grid, per-learner detail, CSV export)."""
import csv
import io
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import ctf
from ..auth import require_admin
from ..db import get_db
from ..models import Agent, Challenge, ChallengeSolve, ChatMessage, ChatSession, HintUnlock, User
from .admin import render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _cell(solve, steps, answer_at, attempts):
    """One learner x one lab. state: done | done_with_answer | answer | stuck | trying | none."""
    if solve:
        state = "done_with_answer" if answer_at and answer_at <= solve.solved_at else "done"
    elif answer_at:
        state = "answer"
    elif steps:
        state = "stuck"
    elif attempts:
        state = "trying"
    else:
        state = "none"
    return {"state": state, "steps": steps, "answer": bool(answer_at), "attempts": attempts,
            "solved_at": solve.solved_at if solve else None, "answer_at": answer_at}


def progress_data(db: Session, module: str = ""):
    chs = [c for c in db.query(Challenge).filter(Challenge.enabled.is_(True))
           .order_by(Challenge.module_order, Challenge.sort_order, Challenge.id) if not module or c.module == module]
    ids = [c.id for c in chs]
    learners = [u for u in db.query(User).filter(User.role != "admin").order_by(User.name, User.email)
                if not u.is_test_account]

    solves = {(s.user_id, s.challenge_id): s for s in db.query(ChallengeSolve).filter(ChallengeSolve.challenge_id.in_(ids))}
    steps, answers = defaultdict(int), {}
    for h in db.query(HintUnlock).filter(HintUnlock.challenge_id.in_(ids)):
        if h.hint_index == ctf.ANSWER_INDEX:
            answers[(h.user_id, h.challenge_id)] = h.created_at
        else:
            steps[(h.user_id, h.challenge_id)] += 1

    # Attempts = messages the learner sent to the lab's agent (v1) or its hardened version.
    agent_ids = {a.slug: a.id for a in db.query(Agent)}
    lab_agents = defaultdict(list)
    for c in chs:
        for slug in (c.agent_slug, c.hardened_slug):
            if slug and slug in agent_ids:
                lab_agents[agent_ids[slug]].append(c.id)
    per_agent = db.query(ChatSession.user_id, ChatSession.agent_id, func.count(ChatMessage.id), func.max(ChatMessage.created_at)) \
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id).filter(ChatMessage.role == "user") \
        .group_by(ChatSession.user_id, ChatSession.agent_id).all()
    attempts, msgs, last = defaultdict(int), defaultdict(int), {}
    for uid, aid, n, ts in per_agent:
        msgs[uid] += n
        last[uid] = max(last.get(uid) or ts, ts)
        for cid in lab_agents.get(aid, []):
            attempts[(uid, cid)] += n
    for uid, ts in db.query(HintUnlock.user_id, func.max(HintUnlock.created_at)).group_by(HintUnlock.user_id):
        last[uid] = max(last.get(uid) or ts, ts)

    rows = []
    for u in learners:
        cells = [_cell(solves.get((u.id, c.id)), steps[(u.id, c.id)], answers.get((u.id, c.id)), attempts[(u.id, c.id)])
                 for c in chs]
        done = sum(x["state"].startswith("done") for x in cells)
        rows.append({"user": u, "cells": cells, "done": done, "pct": round(100 * done / len(chs)) if chs else 0,
                     "steps": sum(x["steps"] for x in cells), "answers": sum(x["answer"] for x in cells),
                     "unaided": sum(x["state"] == "done" and not x["steps"] for x in cells),
                     "messages": msgs[u.id], "last": last.get(u.id) or u.last_login})
    lab_stats = []
    for i, c in enumerate(chs):
        col = [r["cells"][i] for r in rows]
        lab_stats.append({"done": sum(x["state"].startswith("done") for x in col),
                          "stuck": sum(x["state"] in ("stuck", "answer", "trying") for x in col),
                          "answers": sum(x["answer"] for x in col)})
    return chs, rows, lab_stats


@router.get("/progress")
def progress(request: Request, module: str = "", sort: str = "name", db: Session = Depends(get_db),
             user: User = Depends(require_admin)):
    from ..seed.lab_content import MODULES
    chs, rows, lab_stats = progress_data(db, module)
    if sort == "progress":
        rows.sort(key=lambda r: (-r["done"], r["answers"]))
    elif sort == "active":
        rows.sort(key=lambda r: r["last"].timestamp() if r["last"] else 0, reverse=True)
    elif sort == "stuck":
        rows.sort(key=lambda r: (r["done"], -r["steps"]))
    return render(request, "progress.html", user, chs=chs, rows=rows, lab_stats=lab_stats, modules=MODULES,
                  module=module, sort=sort, name=ctf.display_name)


@router.get("/progress.csv")
def progress_csv(module: str = "", db: Session = Depends(get_db)):
    chs, rows, _ = progress_data(db, module)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "labs_done", "labs_total", "percent", "done_without_steps", "steps_opened",
                "answers_viewed", "messages_sent", "last_active"] + [c.slug for c in chs])
    for r in rows:
        u = r["user"]
        w.writerow([ctf.display_name(u), u.email, r["done"], len(chs), r["pct"], r["unaided"], r["steps"], r["answers"],
                    r["messages"], r["last"].isoformat() if r["last"] else ""] + [x["state"] for x in r["cells"]])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="learner-progress.csv"'})


@router.get("/progress/{user_id}")
def progress_learner(user_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    learner = db.get(User, user_id)
    if not learner:
        raise HTTPException(404)
    chs, rows, _ = progress_data(db)
    row = next((r for r in rows if r["user"].id == user_id), None)
    if not row:
        raise HTTPException(404, "Not a learner")
    sessions = {s.agent_id for s in db.query(ChatSession).filter(ChatSession.user_id == user_id)}
    agents = {a.id: a for a in db.query(Agent).filter(Agent.id.in_(sessions))} if sessions else {}
    return render(request, "progress_learner.html", user, learner=learner, row=row, labs=list(zip(chs, row["cells"])),
                  agents=agents.values(), name=ctf.display_name)
