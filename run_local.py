"""Local dev server — ADK API + custom UI + /databases endpoint.

Usage:
    python run_local.py

Then open http://localhost:8081/app/
No CORS issues — UI and API are served from the same origin.

Requires the Docker VPN tunnel running first:
    bash scripts/docker_vpn/run_vpn_tunnel.sh
"""

import os
import socket
import sys
import threading

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from google.adk.cli.fast_api import get_fast_api_app

# ── Resolve agents directory relative to this file ──────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_AGENTS_DIR = os.path.join(_HERE, "src")

# ── Build the ADK FastAPI app ────────────────────────────────────────────────
app: FastAPI = get_fast_api_app(
    agents_dir=_AGENTS_DIR,
    web=False,  # API-only mode (no ADK dev UI)
    allow_origins=["*"],
)

# ── Firebase Auth Middleware ──────────────────────────────────────────────────
# Set AUTH_DISABLED=true to skip auth (useful for local dev without Firebase).
_AUTH_DISABLED = os.environ.get("AUTH_DISABLED", "false").lower() in ("true", "1", "yes")

# Paths that never require authentication
_EXEMPT = ("/app", "/", "/healthz", "/favicon", "/databases")

try:
    import firebase_admin
    from firebase_admin import auth as _fb_auth
    _FIREBASE_AVAILABLE = True
except ImportError:
    _FIREBASE_AVAILABLE = False

_firebase_init_done = False


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
    if request.method == "OPTIONS" or any(path.startswith(p) for p in _EXEMPT):
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
