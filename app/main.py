"""AI Security Range: a training platform for AI vulnerabilities, built around a fictional bank."""
import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import auth
from .config import get_settings
from .db import Base, SessionLocal, engine, migrate
from .mcp_servers.bank import http_app as bank_http_app
from .mcp_servers.bank import mcp as bank_mcp
from .routers import admin, admin_ctf, admin_drive, admin_progress, admin_stack, bank, chat, ctf as ctf_router

log = logging.getLogger("airange")
settings = get_settings()



def _startup():
    if settings.is_production and (settings.secret_key == "change-me" or settings.dev_login_enabled):
        raise RuntimeError("Refusing to start: set SECRET_KEY and disable DEV_LOGIN_ENABLED in production.")
    Base.metadata.create_all(bind=engine)
    migrate()
    from .seed.seed import seed_all
    for attempt in range(30):  # Chroma may still be starting
        db = SessionLocal()
        try:
            seed_all(db)
            break
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning("Seeding not ready yet (%s); retrying", e)
            time.sleep(2)
        finally:
            db.close()


async def _auto_pin_mcp():
    """First run only: pin the clean tool descriptions of registered MCP servers, so hardened agents
    work out of the box. Suspicious tools are never auto-pinned."""
    from .agents.guards import detect_injection
    from .integrations import mcp_client
    from .models import McpServer
    db = SessionLocal()
    try:
        for s in db.query(McpServer).filter(McpServer.enabled.is_(True)):
            if s.approved:
                continue
            tools = (await mcp_client.aload([s])).get(s.slug)
            if isinstance(tools, list):
                s.approved = {t.name: mcp_client.fingerprint(t) for t in tools if not detect_injection(t.description or "")}
                db.commit()
                log.info("Pinned %d clean tool(s) on MCP server %s", len(s.approved), s.slug)
    except Exception as e:  # noqa: BLE001
        log.warning("MCP auto-pin skipped: %s", e)
    finally:
        db.close()


@contextlib.asynccontextmanager
async def lifespan(_app):
    _startup()
    await _auto_pin_mcp()
    async with bank_mcp.session_manager.run():   # the bank's MCP endpoint at /mcp/
        yield


bank_mcp_app = bank_http_app()                     # creates the session manager used in lifespan
app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                   https_only=settings.is_production, max_age=60 * 60 * 12, session_cookie="airange_session")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(auth.router)
app.include_router(bank.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(admin_stack.router)
app.include_router(admin_drive.router)
app.include_router(ctf_router.router)
app.include_router(admin_ctf.router)
app.include_router(admin_progress.router)
app.mount("/mcp", bank_mcp_app)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Content-Security-Policy",
                            "default-src 'self'; img-src 'self' data: https://*.googleusercontent.com; "
                            "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
    return resp


@app.exception_handler(auth.LoginRequired)
async def _login_required(request: Request, exc):
    if request.url.path.startswith("/api/"):
        return HTMLResponse("Sign in required", status_code=401)
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(auth.AdminRequired)
async def _admin_required(request: Request, exc):
    return HTMLResponse("Admins only", status_code=403)


@app.get("/healthz")
def healthz():
    return {"ok": True}
