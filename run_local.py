"""Local dev server — ADK API + custom UI + /databases endpoint.

Usage:
    python run_local.py

Then open http://localhost:8081/app/
No CORS issues — UI and API are served from the same origin.

Requires the Docker VPN tunnel running first:
    bash scripts/docker_vpn/run_vpn_tunnel.sh
"""

import datetime
import os
import socket
import sys
import threading

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app

import logging as _logging
_slog = _logging.getLogger("agentic_rag.server")

try:
    from google.genai.errors import ClientError as _GenAIClientError
except ImportError:
    _GenAIClientError = None

# ── Resolve agents directory relative to this file ──────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR = os.path.join(_HERE, "src")

# ── Build the ADK FastAPI app ────────────────────────────────────────────────
app: FastAPI = get_fast_api_app(
    agents_dir=_AGENTS_DIR,
    web=False,  # API-only mode (no ADK dev UI)
    allow_origins=["*"],
)

# ── keywords that indicate the conversation context window is full ────────────
_OVERFLOW_PHRASES = (
    "request payload size exceeds",
    "context window",
    "too many tokens",
    "token limit",
    "exceeds the limit",
    "maximum context length",
    "input too large",
    "prompt is too long",
)

def _is_overflow(exc_str: str) -> bool:
    low = exc_str.lower()
    return any(phrase in low for phrase in _OVERFLOW_PHRASES)


# ── Gemini rate-limit / context-overflow / API error handler ─────────────────
@app.exception_handler(Exception)
async def _genai_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Convert Gemini API errors into readable JSON with appropriate HTTP codes.

    Status codes chosen so the UI auto-recovery logic can act on them:
      503 — rate-limited (RESOURCE_EXHAUSTED / 429): transient, just retry later.
      422 — context overflow: UI should start a fresh session and retry.
      502 — other Gemini API error: transient.
      500 — unexpected server error.
    """
    exc_str = str(exc)
    _slog.exception("Unhandled error on %s %s", request.method, request.url.path)

    if _GenAIClientError and isinstance(exc, _GenAIClientError):
        if "429" in exc_str or "RESOURCE_EXHAUSTED" in exc_str:
            return JSONResponse(
                {"error": "The AI model is currently busy. Please wait a moment and try again."},
                status_code=503,
            )
        if _is_overflow(exc_str):
            return JSONResponse(
                {"error": "CONTEXT_OVERFLOW: conversation history is too long. Starting a fresh session."},
                status_code=422,
            )
        return JSONResponse(
            {"error": "AI model error. Please try again."},
            status_code=502,
        )

    # Non-ClientError but still looks like a context overflow
    if _is_overflow(exc_str):
        return JSONResponse(
            {"error": "CONTEXT_OVERFLOW: conversation history is too long. Starting a fresh session."},
            status_code=422,
        )

    # All other unhandled exceptions → generic 500
    return JSONResponse({"error": "Internal server error. Please try again."}, status_code=500)


# Explicit CORS middleware so Authorization header is allowed on preflight
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Firebase Auth Middleware ──────────────────────────────────────────────────
# Set AUTH_DISABLED=true to skip auth (useful for local dev without Firebase).
_AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "false").lower() in ("true", "1", "yes")

# Paths that never require authentication
# NOTE: "/" must be an exact match — using startswith("/") would exempt ALL paths.
_EXEMPT_EXACT = {"/"}
_EXEMPT_PREFIX = ("/app", "/healthz", "/favicon", "/databases")

try:
    import firebase_admin
    from firebase_admin import auth as _fb_auth
    _FIREBASE_AVAILABLE = True
except ImportError:
    _FIREBASE_AVAILABLE = False

_firebase_init_done = False
_ADMIN_EMAIL = "sbheema@swardesi.com"
_ONLINE_WINDOW_SEC = 120  # seconds before a user is considered offline
_firestore_client = None


def _get_firestore():
    """Lazy-init a shared Firestore client."""
    global _firestore_client
    if _firestore_client is None:
        from google.cloud import firestore
        _firestore_client = firestore.Client(project="unicon-494419")
    return _firestore_client


def _init_firebase_once() -> None:
    global _firebase_init_done
    if _firebase_init_done or not _FIREBASE_AVAILABLE:
        return
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": "unicon-494419"})
    _firebase_init_done = True


@app.middleware("http")
async def firebase_auth_middleware(request: Request, call_next):
    """Validate Firebase ID tokens on API routes. Exempt static files & health checks."""
    if _AUTH_DISABLED or not _FIREBASE_AVAILABLE:
        return await call_next(request)

    path = request.url.path
    if request.method == "OPTIONS" or path in _EXEMPT_EXACT or any(path.startswith(p) for p in _EXEMPT_PREFIX):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Authentication required"}, status_code=401)

    token = auth_header[7:]
    try:
        _init_firebase_once()
        decoded = _fb_auth.verify_id_token(token)
        request.state.user_email = decoded.get("email", "")
        request.state.user_uid = decoded.get("uid", "")
    except Exception:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)

    return await call_next(request)


# ── /ping — presence heartbeat (every 30 s from the UI) ─────────────────────
@app.post("/ping")
async def presence_ping(request: Request) -> JSONResponse:
    """Record that the authenticated user is currently active."""
    uid = getattr(request.state, "user_uid", "")
    email = getattr(request.state, "user_email", "")
    if not uid:
        return JSONResponse({"ok": False}, status_code=401)
    try:
        from google.cloud import firestore
        db = _get_firestore()
        db.collection("user_presence").document(uid).set(
            {"email": email, "lastActive": firestore.SERVER_TIMESTAMP},
            merge=True,
        )
    except Exception as exc:
        import logging as _lg
        _lg.getLogger(__name__).warning("Presence ping failed: %s", exc)
    return JSONResponse({"ok": True})


# ── /admin/users — active users + last login (admin only) ─────────────────────
@app.get("/admin/users")
async def admin_users(request: Request) -> JSONResponse:
    """Return all Firebase Auth users with last-login and online status.
    Restricted to the admin account only."""
    user_email = getattr(request.state, "user_email", "")
    if user_email != _ADMIN_EMAIL:
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    if not _FIREBASE_AVAILABLE:
        return JSONResponse({"error": "Firebase Admin not available"}, status_code=503)
    _init_firebase_once()

    # ── 1. Presence data (Firestore) ─────────────────────────────────────────
    online_cutoff = datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(seconds=_ONLINE_WINDOW_SEC)
    presence: dict[str, dict] = {}
    try:
        db = _get_firestore()
        for doc in db.collection("user_presence").stream():
            d = doc.to_dict()
            la = d.get("lastActive")
            if la is not None:
                if la.tzinfo is None:
                    la = la.replace(tzinfo=datetime.timezone.utc)
                presence[doc.id] = {
                    "lastActive": la.isoformat(),
                    "isOnline": la >= online_cutoff,
                }
    except Exception as exc:
        import logging as _lg
        _lg.getLogger(__name__).warning("Firestore presence read failed: %s", exc)

    # ── 2. Firebase Auth user list ────────────────────────────────────────────
    users: list[dict] = []
    try:
        page = _fb_auth.list_users()
        while page:
            for u in page.users:
                meta = u.user_metadata
                last_sign_in_ms = getattr(meta, "last_sign_in_time", None) if meta else None
                last_login_iso: str | None = None
                if last_sign_in_ms:
                    try:
                        last_login_iso = datetime.datetime.fromtimestamp(
                            last_sign_in_ms / 1000, tz=datetime.timezone.utc
                        ).isoformat()
                    except Exception:
                        pass
                p = presence.get(u.uid, {})
                users.append({
                    "uid": u.uid,
                    "email": u.email or "",
                    "displayName": u.display_name or "",
                    "photoURL": u.photo_url or "",
                    "lastLogin": last_login_iso,
                    "lastActive": p.get("lastActive"),
                    "isOnline": p.get("isOnline", False),
                    "disabled": u.disabled,
                })
            page = page.get_next_page()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)

    users.sort(key=lambda u: u["lastLogin"] or "", reverse=True)
    return JSONResponse({"users": users, "total": len(users)})


# ── /databases — populates the Active Database dropdown in the UI ─────────────
@app.get("/databases")
def list_databases() -> JSONResponse:
    """Return all connections from connections.json (no credentials exposed)."""
    try:
        from agentic_rag.connections import default_alias, list_connections
        connections = list_connections()
        default = default_alias()
    except Exception as exc:
        return JSONResponse({"connections": [], "default": "", "error": str(exc)})

    return JSONResponse({"connections": connections, "default": default})


@app.get("/salespersons")
def salespersons_list(db_alias: str = "") -> JSONResponse:
    """Return distinct salesperson IDs + names for the sidebar dropdown."""
    from agentic_rag.connections import default_alias, get_connection, resolve_password

    alias = db_alias or default_alias()
    cfg = get_connection(alias)
    if not cfg:
        return JSONResponse({"salespersons": []})

    db_type = cfg.get("db_type", "postgres")
    host = cfg.get("host", "")
    port = int(cfg.get("port", 1433))
    database = cfg.get("database", "")
    user = cfg.get("user", "")
    try:
        pw = resolve_password(cfg)
    except Exception:
        return JSONResponse({"salespersons": []})

    sql = (
        "SELECT DISTINCT salesperson_id, salesperson_name "
        "FROM vw_salesperson_orders_summary "
        "ORDER BY salesperson_name"
    )
    try:
        if db_type == "mssql":
            import pymssql  # type: ignore
            cn = pymssql.connect(
                server=host, port=str(port), user=user,
                password=pw, database=database, timeout=8,
            )
        else:
            import psycopg2  # type: ignore
            cn = psycopg2.connect(
                host=host, port=port, user=user,
                password=pw, dbname=database, connect_timeout=8,
            )
        cur = cn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cn.close()
        result = [
            {"id": str(r[0]), "name": str(r[1] or r[0])}
            for r in rows
            if r[0]
        ]
        return JSONResponse({"salespersons": result})
    except Exception as exc:
        return JSONResponse({"salespersons": [], "error": str(exc)})


@app.get("/healthz/db")
def healthz_db() -> JSONResponse:
    """Diagnostic: test TCP connectivity to all configured DB hosts."""
    try:
        from agentic_rag.connections import get_connection, list_connections
        conns = list_connections()
    except Exception as exc:
        return JSONResponse({"error": str(exc)})
    results = {}
    for conn in conns:
        alias = conn.get("alias", "?")
        full = get_connection(alias) or {}
        host = full.get("host", "")
        port = int(full.get("port", 1433))
        if not host:
            results[alias] = "SKIP: no host"
            continue
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            results[alias] = f"REACHABLE ({host}:{port})"
        except Exception as exc:
            results[alias] = f"FAILED ({host}:{port}): {exc}"
    return JSONResponse({"db_connectivity": results})


# ── Serve UI static files ──────────────────────────────────────────────────
_UI_DIR = os.path.join(_HERE, "ui")
if os.path.isdir(_UI_DIR):
    app.mount("/app", StaticFiles(directory=_UI_DIR, html=True), name="ui")


@app.get("/")
def _root_redirect() -> RedirectResponse:
    return RedirectResponse(url="/app/")


# ── Pre-warm schema cache in background so first query is instant ─────────────
def _prewarm() -> None:
    try:
        from agentic_rag.agent import prewarm_schema_cache
        prewarm_schema_cache()
    except Exception as exc:
        print(f"[prewarm] warning: {exc}")

threading.Thread(target=_prewarm, daemon=True, name="schema-prewarm").start()


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    print(f"\n{'='*60}")
    print(f"  Open: http://localhost:{port}/app/")
    print(f"  API : http://localhost:{port}")
    print(f"{'='*60}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
