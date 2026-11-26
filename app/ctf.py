"""CTF engine: per-player flags, scoring with first blood and hint costs, and the event window.

Flags are derived per player (HMAC of the player and the challenge), so sharing a flag doesn't help:
a flag submitted by the wrong player is rejected and logged. Text that agents or files show can carry
`{{FLAG:<slug>}}` placeholders, which are rendered with the current player's flag.
"""
import hashlib
import hmac
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func

from .config import get_settings
from .models import Challenge, ChallengeSolve, FlagSubmission, HintUnlock, Setting, User

FLAG_RE = re.compile(r"MERIDIAN\{[A-Za-z0-9_-]+\}")
PLACEHOLDER = re.compile(r"\{\{FLAG:([a-z0-9-]+)\}\}")
FIRST_BLOOD_BONUS = 0.10
DEFAULT_EVENT = {"name": "Meridian Bank AI Security Labs", "timezone": "Asia/Kolkata", "start": "", "end": "",
                 "freeze": "", "scoreboard_public": True, "show_flags_on_solve": True,
                 "mode": "labs",          # labs: guided, free steps, worked answers | ctf: points, paid hints, scoreboard
                 "answers": "always"}     # labs mode: always | after_steps | after_completion | hidden
ANSWER_INDEX = -1                         # HintUnlock index used to record "answer revealed"
STEP_BASE = 100                           # HintUnlock index 100+i records "step i revealed" (labs mode, free)


def labs_mode(db) -> bool:
    return event(db).get("mode", "labs") == "labs"


def reveal(db, user, ch, index: int):
    """Record a free reveal (lab step or answer) so trainers can see who needed help."""
    if not db.query(HintUnlock).filter_by(user_id=user.id, challenge_id=ch.id, hint_index=index).first():
        db.add(HintUnlock(user_id=user.id, challenge_id=ch.id, hint_index=index, cost=0))
        db.commit()


def answer_allowed(db, user, ch, solved: bool, steps_seen: int) -> tuple[bool, str]:
    policy = event(db).get("answers", "always")
    if user.is_admin or policy == "always":
        return True, ""
    if policy == "after_steps":
        return steps_seen >= len(ch.steps or []), "Reveal all the guided steps first."
    if policy == "after_completion":
        return solved, "The answer unlocks once you complete the lab."
    return False, "Your trainer has hidden the answers for this session."


def _key() -> bytes:
    return hashlib.sha256(("ctf-flags:" + get_settings().secret_key).encode()).digest()


def flag_core(user_id: int, slug: str) -> str:
    return hmac.new(_key(), f"{user_id}:{slug}".encode(), hashlib.sha256).hexdigest()[:12]


def flag_for(user_id: int, slug: str) -> str:
    return f"MERIDIAN{{{slug}-{flag_core(user_id, slug)}}}"


def render_flags(text: str, user) -> str:
    if not text or "{{FLAG:" not in text:
        return text
    return PLACEHOLDER.sub(lambda m: flag_for(user.id, m.group(1)), text)


# ---------------------------------------------------------------- event window

def event(db) -> dict:
    row = db.get(Setting, "ctf")
    return {**DEFAULT_EVENT, **((row.value if row else None) or {})}


def _parse(value: str, tz: str):
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz or "UTC"))


def window(db) -> dict:
    ev = event(db)
    now = datetime.now(timezone.utc)
    start, end, freeze = (_parse(ev.get(k, ""), ev["timezone"]) for k in ("start", "end", "freeze"))
    state = "running"
    if start and now < start:
        state = "not_started"
    elif end and now >= end:
        state = "ended"
    return {"event": ev, "state": state, "start": start, "end": end, "freeze": freeze, "now": now,
            "frozen": bool(freeze and now >= freeze and state != "not_started")}


def scoring_open(db) -> bool:
    return window(db)["state"] == "running"


# ---------------------------------------------------------------- solving

def award(db, user, ch: Challenge, agent_id=None, evidence: str = "") -> ChallengeSolve | None:
    """Record a solve once. Returns the new solve, or None if already solved or the event isn't running."""
    if db.query(ChallengeSolve).filter_by(user_id=user.id, challenge_id=ch.id).first():
        return None
    if not scoring_open(db):
        return None
    first = db.query(ChallengeSolve).filter_by(challenge_id=ch.id).count() == 0
    pts = ch.points + (round(ch.points * FIRST_BLOOD_BONUS) if first else 0)
    s = ChallengeSolve(user_id=user.id, challenge_id=ch.id, agent_id=agent_id, evidence=evidence[:500],
                       points=pts, first_blood=first)
    db.add(s)
    db.commit()
    return s


def submit_flag(db, user, value: str) -> dict:
    """Check a submitted flag against every flag challenge for this player."""
    value = (value or "").strip()
    m = FLAG_RE.search(value)
    if not m:
        db.add(FlagSubmission(user_id=user.id, value=value[:200], correct=False, note="not a flag format"))
        db.commit()
        return {"ok": False, "msg": "That doesn't look like a flag. Flags look like MERIDIAN{...}."}
    flag = m.group(0)
    for ch in db.query(Challenge).filter_by(enabled=True):
        if hmac.compare_digest(flag, flag_for(user.id, ch.slug)):
            if ch.kind != "flag":
                # detector challenges also show a flag; accept it as proof
                pass
            s = award(db, user, ch, evidence=f"flag submitted: {flag}")
            db.add(FlagSubmission(user_id=user.id, challenge_id=ch.id, value=flag, correct=True))
            db.commit()
            if s is None:
                already = db.query(ChallengeSolve).filter_by(user_id=user.id, challenge_id=ch.id).first()
                return {"ok": bool(already), "msg": f"Correct, but {'you already solved' if already else 'scoring is closed for'} “{ch.title}”."}
            fb = " First blood! 🩸" if s.first_blood else ""
            return {"ok": True, "msg": f"Correct! “{ch.title}” solved for {s.points} points.{fb}", "challenge": ch.slug}
    # someone else's flag?
    note = ""
    slug = flag[len("MERIDIAN{"):-1].rsplit("-", 1)[0]
    ch = db.query(Challenge).filter_by(slug=slug).first()
    if ch:
        for (uid,) in db.query(User.id).all():
            if uid != user.id and hmac.compare_digest(flag, flag_for(uid, slug)):
                note = f"flag belongs to user #{uid}"
                break
    db.add(FlagSubmission(user_id=user.id, challenge_id=ch.id if ch else None, value=flag, correct=False, note=note))
    db.commit()
    if note:
        return {"ok": False, "msg": "That flag belongs to another player. Flags are personal; this attempt was logged."}
    return {"ok": False, "msg": "Incorrect flag."}


def unlock_hint(db, user, ch: Challenge, index: int) -> tuple[bool, str]:
    hints = ch.hints or []
    if not 0 <= index < len(hints):
        return False, "No such hint."
    if db.query(HintUnlock).filter_by(user_id=user.id, challenge_id=ch.id, hint_index=index).first():
        return True, "Already unlocked."
    cost = int(hints[index].get("cost", 0))
    db.add(HintUnlock(user_id=user.id, challenge_id=ch.id, hint_index=index, cost=cost))
    db.commit()
    return True, f"Hint unlocked ({cost} points)."


# ---------------------------------------------------------------- scoreboard

def scoreboard(db, include_admins: bool = False, respect_freeze: bool = True) -> list[dict]:
    w = window(db)
    q = db.query(ChallengeSolve)
    if respect_freeze and w["frozen"]:
        q = q.filter(ChallengeSolve.solved_at < w["freeze"])
    solves = q.all()
    costs = dict(db.query(HintUnlock.user_id, func.sum(HintUnlock.cost)).group_by(HintUnlock.user_id).all())
    users = {u.id: u for u in db.query(User)}
    rows = {}
    for s in solves:
        u = users.get(s.user_id)
        if not u or (u.role == "admin" and not include_admins):
            continue
        r = rows.setdefault(u.id, {"user": u, "points": 0, "solves": 0, "first_bloods": 0, "last": s.solved_at})
        r["points"] += s.points
        r["solves"] += 1
        r["first_bloods"] += int(s.first_blood)
        r["last"] = max(r["last"], s.solved_at)
    for uid, r in rows.items():
        r["hint_cost"] = int(costs.get(uid, 0) or 0)
        r["points"] -= r["hint_cost"]
    ranked = sorted(rows.values(), key=lambda r: (-r["points"], r["last"]))
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return ranked


def player_score(db, user) -> int:
    pts = sum(s.points for s in db.query(ChallengeSolve).filter_by(user_id=user.id))
    cost = sum(h.cost for h in db.query(HintUnlock).filter_by(user_id=user.id))
    return pts - cost


def display_name(u) -> str:
    """Public name on the scoreboard: first name + initial, never the email."""
    parts = (u.name or u.email.split("@")[0]).split()
    return parts[0] + (f" {parts[-1][0]}." if len(parts) > 1 else "")
