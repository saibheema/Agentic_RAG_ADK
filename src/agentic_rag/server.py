"""Custom FastAPI server: ADK agents + /databases endpoint.

Wraps the standard ADK api_server and adds a /databases route so the UI
can discover available DB connections at runtime.

Run locally:
    uvicorn agentic_rag.server:app --port 8081 --reload
or:
    python -m agentic_rag.server

Then point the UI's API Base URL to http://localhost:8081
"""

from __future__ import annotations

import logging
import os
import socket

import uvicorn
from fastapi import Request
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app

from agentic_rag.connections import default_alias, list_connections

_log = logging.getLogger(__name__)

try:
    from google.genai.errors import ClientError as _GenAIClientError
except ImportError:
    _GenAIClientError = None

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


# agents_dir is the parent of the agentic_rag package (i.e. "src/").
_AGENTS_DIR = os.environ.get("AGENTS_DIR", "src")

app = get_fast_api_app(
    agents_dir=_AGENTS_DIR,
    allow_origins=["*"],
    web=False,
)


@app.exception_handler(Exception)
async def _error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Mirror the run_local.py error handler for Cloud Run deployments."""
    exc_str = str(exc)
    _log.exception("Unhandled error on %s %s", request.method, request.url.path)
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
    if _is_overflow(exc_str):
        return JSONResponse(
            {"error": "CONTEXT_OVERFLOW: conversation history is too long. Starting a fresh session."},
            status_code=422,
        )
    return JSONResponse({"error": "Internal server error. Please try again."}, status_code=500)


@app.get("/databases")
def databases() -> JSONResponse:
    """Return available DB connections — alias, label, db_type (no credentials).

    Called by the UI on startup to populate the DB selector dropdown.
    """
    return JSONResponse(
        {
            "connections": list_connections(),
            "default": default_alias(),
        }
    )


@app.get("/salespersons")
def salespersons_list(db_alias: str = "") -> JSONResponse:
    """Return distinct salesperson IDs and names for the sidebar dropdown."""
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
    """Diagnostic: test TCP connectivity to configured DB hosts."""
    results = {}
    for conn in list_connections():
        alias = conn.get("alias", "?")
        host = conn.get("host", "")
        port = int(conn.get("port", 1433))
        try:
            s = socket.create_connection((host, port), timeout=5)
            s.close()
            results[alias] = "REACHABLE"
        except Exception as exc:
            results[alias] = f"FAILED: {exc}"
    return JSONResponse({"db_connectivity": results})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8081"))
    uvicorn.run("agentic_rag.server:app", host="0.0.0.0", port=port, reload=False)
