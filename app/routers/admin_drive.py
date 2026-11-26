"""Admin pages for MeriDrive (the bank's files), vendor payments and candidate screenings."""
from collections import defaultdict

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..db import get_db
from ..drive import store as dstore
from ..drive.extract import ALLOWED_EXT, ext_of, hidden_diff
from ..models import Agent, CandidateScreening, DriveFile, User, VendorPayment
from ..security import verify_csrf
from .admin import audit, back, flash, render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def _agents_by_scope(db) -> dict:
    out = defaultdict(list)
    for a in db.query(Agent).order_by(Agent.sort_order):
        sc = (a.controls or {}).get("file_scope")
        if sc and any(t in (a.tools or []) for t in ("read_file", "list_files", "search_files")):
            out[sc].append(a.slug)
    return out


@router.get("/files")
def files(request: Request, folder: str = "", db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rows = db.query(DriveFile).order_by(DriveFile.path).all()
    tree = defaultdict(list)
    for f in rows:
        tree[f.path.rsplit("/", 1)[0] if "/" in f.path else ""].append(f)
    scopes = _agents_by_scope(db)
    stats = {"files": len(rows), "poisoned": sum(1 for f in rows if f.poisoned),
             "hidden": sum(1 for f in rows if f.text_full.strip() != f.text_visible.strip()),
             "restricted": sum(1 for f in rows if f.access not in ("public",))}
    return render(request, "files.html", user, tree=dict(sorted(tree.items())), scopes=scopes, stats=stats,
                  levels=dstore.ACCESS_LEVELS, allowed=sorted(ALLOWED_EXT), folder=folder,
                  role_access=dstore.ROLE_FILE_ACCESS)


@router.get("/files/view")
def file_view(request: Request, path: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = db.query(DriveFile).filter_by(path=path).first()
    if not f:
        return back("/admin/files")
    hidden = hidden_diff(f.text_full, f.text_visible)
    readers = [a for a in db.query(Agent) if any(t in (a.tools or []) for t in ("read_file", "search_files"))]
    can = [a.slug for a in readers if not (a.controls or {}).get("file_scope")
           or path.startswith(((a.controls or {}).get("file_scope") or "").split("{")[0])]
    return render(request, "file_view.html", user, f=f, hidden=hidden, levels=dstore.ACCESS_LEVELS, agents=can)


@router.get("/files/download")
def file_download(path: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = db.query(DriveFile).filter_by(path=path).first()
    return FileResponse(dstore.disk_path(f), filename=f.path.rsplit("/", 1)[-1])


@router.post("/files/upload")
async def file_upload(request: Request, folder: str = Form(""), title: str = Form(""), access: str = Form("staff"),
                      owner: str = Form(""), description: str = Form(""), poisoned: str = Form(""),
                      file: UploadFile = File(...), csrf: str = Form(""), db: Session = Depends(get_db),
                      user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    data = await file.read()
    name = dstore.safe_name(file.filename or "upload.txt")
    path = dstore.normalize(f"{folder.strip('/')}/{name}" if folder.strip() else name)
    if access not in dstore.ACCESS_LEVELS or ext_of(name) not in ALLOWED_EXT:
        flash(request, f"Allowed types: {', '.join(sorted(ALLOWED_EXT))}", "error")
        return back("/admin/files")
    try:
        f = dstore.save_file(db, path, data, title=title or name, access=access, owner=owner.strip(),
                             description=description, poisoned=poisoned, uploaded_by=user.email)
        n = dstore.index_file(db, f)
    except ValueError as e:
        flash(request, str(e), "error")
        return back("/admin/files")
    audit(db, user, f"MeriDrive: uploaded {path}", access=access)
    flash(request, f"Uploaded {path} ({n} chunks indexed).")
    return back(f"/admin/files/view?path={path}")


@router.post("/files/meta")
def file_meta(request: Request, path: str = Form(...), title: str = Form(""), access: str = Form(...),
              owner: str = Form(""), description: str = Form(""), poisoned: str = Form(""), csrf: str = Form(""),
              db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    f = db.query(DriveFile).filter_by(path=path).first()
    if access in dstore.ACCESS_LEVELS:
        f.access = access
    f.title, f.owner, f.description, f.poisoned = title or f.title, owner.strip(), description, poisoned
    db.commit()
    dstore.index_file(db, f)       # access level lives in the vector metadata too
    audit(db, user, f"MeriDrive: updated {path}", access=f.access)
    flash(request, "Saved and re-indexed.")
    return back(f"/admin/files/view?path={path}")


@router.post("/files/delete")
def file_delete(request: Request, path: str = Form(...), csrf: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    f = db.query(DriveFile).filter_by(path=path).first()
    if f:
        dstore.delete_file(db, f)
        audit(db, user, f"MeriDrive: deleted {path}")
    flash(request, f"Deleted {path}.")
    return back("/admin/files")


@router.post("/files/reindex")
def files_reindex(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    n = sum(dstore.index_file(db, f) for f in db.query(DriveFile))
    audit(db, user, "MeriDrive: re-indexed all files")
    flash(request, f"Re-indexed {n} chunks into {dstore.COLLECTION}.")
    return back("/admin/files")


@router.post("/files/reset")
def files_reset(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    n = dstore.reset_to_seed(db)
    db.query(VendorPayment).delete()
    db.query(CandidateScreening).delete()
    db.commit()
    audit(db, user, "MeriDrive: reset to the seed files; cleared payments and screenings")
    flash(request, f"Drive reset: {n} seed files restored; payments and screenings cleared. "
                   "Learner statement files are recreated at their next sign-in.")
    return back("/admin/files")


# ---------------------------------------------------------------- payments and screenings

@router.get("/payments")
def payments(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    from ..agents.tools import vendor_master
    rows = db.query(VendorPayment).order_by(VendorPayment.id.desc()).limit(200).all()
    users = {u.id: u for u in db.query(User)}
    agents = {a.id: a for a in db.query(Agent)}
    return render(request, "payments.html", user, rows=rows, users=users, agents=agents, master=vendor_master(db))


@router.post("/payments/{pid}/{action}")
def payment_action(pid: int, action: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    p = db.get(VendorPayment, pid)
    if p and p.status in ("pending_approval", "blocked") and action in ("approve", "reject"):
        p.status = "approved" if action == "approve" else "rejected"
        db.commit()
        audit(db, user, f"Payment #{pid} {p.status}", vendor=p.vendor, amount=p.amount, account=p.account_no)
        flash(request, f"Payment #{pid} {p.status}.")
    return back("/admin/payments")


@router.get("/screenings")
def screenings(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rows = db.query(CandidateScreening).order_by(CandidateScreening.id.desc()).limit(200).all()
    users = {u.id: u for u in db.query(User)}
    agents = {a.id: a for a in db.query(Agent)}
    return render(request, "screenings.html", user, rows=rows, users=users, agents=agents)
