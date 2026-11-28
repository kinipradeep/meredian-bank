"""Platform branding: the name and logo of the training platform around the fictional bank.

The CollabAce logo ships with the code (app/static/brand). An admin can upload another in Admin > Settings >
Branding; uploads live outside the code folder (next to the drive files) so updates never remove them.
Only PNG, JPEG and WebP are accepted: an SVG served from the range's own origin could carry script.
"""
from pathlib import Path

DEFAULT_NAME = "CollabAce AI Cyber Range"
DEFAULT_LOGO = "/static/brand/collabace-logo.png"
DEFAULT_MARK = "/static/brand/collabace-mark.png"
MAX_BYTES = 1_000_000
TYPES = {b"\x89PNG\r\n\x1a\n": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg")}
_cache: dict = {}


def brand_dir() -> Path:
    from .config import get_settings
    d = Path(get_settings().files_root).parent / "brand"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sniff(data: bytes):
    """(extension, mime type) for an accepted image, else None."""
    for magic, kind in TYPES.items():
        if data.startswith(magic):
            return kind
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ("webp", "image/webp")
    return None


def logo_file() -> Path | None:
    for ext in ("png", "jpg", "webp"):
        p = brand_dir() / f"logo.{ext}"
        if p.exists() and p.stat().st_size:
            return p
    return None


def save_logo(data: bytes) -> str:
    kind = sniff(data)
    if not kind:
        raise ValueError("The logo must be a PNG, JPEG or WebP image.")
    if len(data) > MAX_BYTES:
        raise ValueError("The logo must be under 1 MB.")
    remove_logo()
    (brand_dir() / f"logo.{kind[0]}").write_bytes(data)
    _cache.clear()
    return kind[0]


def remove_logo():
    for ext in ("png", "jpg", "webp"):
        (brand_dir() / f"logo.{ext}").unlink(missing_ok=True)
    _cache.clear()


def set_name(db, name: str):
    from .models import Setting
    row = db.get(Setting, "branding") or Setting(key="branding", value={})
    row.value = {**(row.value or {}), "name": name.strip()[:80] or DEFAULT_NAME}
    db.add(row)
    db.commit()
    _cache.clear()


def brand() -> dict:
    """{'name', 'logo' (URL or None)} for templates; cached until branding changes."""
    if not _cache:
        name = DEFAULT_NAME
        try:
            from .db import SessionLocal
            from .models import Setting
            with SessionLocal() as db:
                row = db.get(Setting, "branding")
                name = ((row.value or {}).get("name") if row else None) or DEFAULT_NAME
        except Exception:  # noqa: BLE001  the database may not be up yet
            pass
        f = logo_file()
        # An uploaded logo wins; otherwise the CollabAce logo bundled with the code.
        _cache.update(name=name, logo=f"/brand/logo?v={int(f.stat().st_mtime)}" if f else DEFAULT_LOGO,
                      custom_logo=bool(f), mark=DEFAULT_MARK)
    return dict(_cache)
