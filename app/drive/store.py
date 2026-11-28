"""MeriDrive: the bank's document drive that agents read from.

Files live on disk under FILES_ROOT (bytes) and in the drive_files table (metadata + extracted text).
They are also chunked into the Chroma collection `meridian-drive` for search_files.
Agents only ever reach files through registered paths: nothing here opens arbitrary disk paths.
"""
import hashlib
import json
import posixpath
import shutil
from pathlib import Path

from ..config import get_settings
from ..models import DriveFile
from .extract import ALLOWED_EXT, ext_of, extract

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "drive"
COLLECTION = "meridian-drive"
ACCESS_LEVELS = ["public", "customer", "staff", "finance", "hr", "it", "board"]
ROLE_FILE_ACCESS = {
    "customer": ["public"],
    "staff": ["public", "staff", "finance"],
    "hr": ["public", "staff", "hr"],
    "admin": ACCESS_LEVELS,
}
MAX_BYTES = 10 * 1024 * 1024


def root() -> Path:
    r = Path(get_settings().files_root)
    r.mkdir(parents=True, exist_ok=True)
    return r


def disk_path(f: DriveFile) -> Path:
    p = (root() / f.path).resolve()
    if root().resolve() not in p.parents:
        raise ValueError("path escapes the drive root")
    return p


def normalize(path: str) -> str:
    """Canonical drive path: forward slashes, no leading slash, '..' resolved inside the drive."""
    p = posixpath.normpath("/" + (path or "").replace("\\", "/").strip()).lstrip("/")
    return "" if p == "." else p


def can_read(user, f: DriveFile) -> bool:
    if f.access == "customer":
        return user.role == "admin" or (f.owner and f.owner == user.customer_id)
    return f.access in ROLE_FILE_ACCESS.get(user.role, ["public"])


def safe_name(name: str) -> str:
    keep = "".join(ch if ch.isalnum() or ch in "-_.()" else "-" for ch in name.strip())
    return keep.strip(".-") or "file"


def save_file(db, path: str, data: bytes, *, title="", access="staff", owner="", description="", poisoned="",
              tags=(), uploaded_by="seed") -> DriveFile:
    path = normalize(path)
    if not path or ext_of(path) not in ALLOWED_EXT:
        raise ValueError(f"unsupported file type: {path}")
    if len(data) > MAX_BYTES:
        raise ValueError("file too large (10 MB max)")
    f = db.query(DriveFile).filter_by(path=path).first() or DriveFile(path=path)
    target = (root() / path).resolve()
    if root().resolve() not in target.parents:
        raise ValueError("path escapes the drive root")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    f.title = title or f.title or Path(path).name
    f.access, f.owner, f.description = access, owner, description
    f.poisoned, f.tags, f.uploaded_by = poisoned, list(tags), uploaded_by
    f.size, f.sha256 = len(data), hashlib.sha256(data).hexdigest()
    f.text_full = extract(path, data)
    f.text_visible = extract(path, data, visible=True)
    db.add(f)
    db.commit()
    return f


def delete_file(db, f: DriveFile):
    try:
        disk_path(f).unlink(missing_ok=True)
    except ValueError:
        pass
    unindex(f)
    db.delete(f)
    db.commit()


# ---------------------------------------------------------------- vector index

def _embedder(db):
    from ..rag import store as vstore
    from ..rag.embeddings import get_embedder
    return get_embedder(vstore.collection_embedder(COLLECTION), db)


def index_file(db, f: DriveFile) -> int:
    from ..rag import store as vstore
    from ..rag.chunking import chunk_text
    unindex(f)
    chunks = chunk_text(f.text_full, "paragraph", 700, 80)
    if not chunks:
        return 0
    texts = [c["text"] for c in chunks]
    vstore.add_chunks(COLLECTION, [f"file{f.id}-c{c['index']}" for c in chunks], texts, _embedder(db).embed(texts),
                      [{"file_id": f.id, "doc_id": f.id, "title": f.title, "path": f.path, "access": f.access,
                        "owner": f.owner or "", "chunk_index": c["index"]} for c in chunks])
    return len(chunks)


def unindex(f: DriveFile):
    from ..rag import store as vstore
    try:
        vstore.get_collection(COLLECTION).delete(where={"file_id": f.id})
    except Exception:  # noqa: BLE001
        pass


def search(db, query: str, k: int, user=None) -> list[dict]:
    """Semantic search over the drive. With `user`, results are filtered to files they may read."""
    from ..rag import store as vstore
    col = vstore.get_collection(COLLECTION)
    where = None
    if user is not None:
        allowed = ROLE_FILE_ACCESS.get(user.role, ["public"])
        where = {"$or": [{"access": {"$in": allowed}}, {"owner": user.customer_id or "-"}]}
    res = col.query(query_embeddings=_embedder(db).embed([query]), n_results=k, where=where,
                    include=["documents", "metadatas", "distances"])
    return [{"path": md.get("path"), "title": md.get("title"), "access": md.get("access"), "text": doc,
             "similarity": round(1 - float(dist), 4)}
            for doc, md, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])]


# ---------------------------------------------------------------- seeding

def seed_drive(db, embedder: str) -> int:
    """Copy the seed files into the drive (once) and index them. Admin edits are never overwritten."""
    from ..rag import store as vstore
    vstore.ensure_collection(COLLECTION, embedder=embedder, description="MeriDrive: the bank's documents")
    manifest = json.loads((SEED_DIR / "manifest.json").read_text())
    added = 0
    for m in manifest:
        if db.query(DriveFile).filter_by(path=m["path"]).first():
            continue
        f = save_file(db, m["path"], (SEED_DIR / m["path"]).read_bytes(), title=m["title"], access=m["access"],
                      owner=m.get("owner", ""), description=m.get("description", ""), poisoned=m.get("poisoned", ""),
                      tags=m.get("tags", []))
        index_file(db, f)
        added += 1
    heal_index(db)
    return added


def heal_index(db) -> int:
    """Index any drive file that has no chunks in the vector database.

    Seeding skips files already in the database, so if Chroma was not ready on the first start, or lost its data
    later, those files would never be searchable again (search_files finds nothing; the board-leak lab can't work).
    """
    from ..rag import store as vstore
    try:
        got = vstore.get_collection(COLLECTION).get(include=["metadatas"])
    except Exception:  # noqa: BLE001  vector DB down: try again at the next start
        return 0
    indexed = {md.get("file_id") for md in (got.get("metadatas") or []) if md}
    missing = [f for f in db.query(DriveFile).all() if f.id not in indexed and (f.text_full or "").strip()]
    for f in missing:
        index_file(db, f)
    return len(missing)


def reset_to_seed(db) -> int:
    """Remove every drive file and re-import the seed set (trainer 'reset the lab')."""
    for f in db.query(DriveFile).all():
        delete_file(db, f)
    shutil.rmtree(root(), ignore_errors=True)
    return seed_drive(db, get_settings().default_embedder)
