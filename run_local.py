"""Local dev server — ADK API + custom UI + /databases endpoint.

Usage:
    python run_local.py

Then open http://localhost:8081/app/
No CORS issues — UI and API are served from the same origin.

Requires the Docker VPN tunnel running first:
    bash scripts/docker_vpn/run_vpn_tunnel.sh
"""

import os
import sys
import threading

import uvicorn
from fastapi import FastAPI
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


# ── Serve custom UI at /app (same origin = no CORS needed) ──────────────────
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
