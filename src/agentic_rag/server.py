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

import os
import socket

import uvicorn
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app

from agentic_rag.connections import default_alias, list_connections

# agents_dir is the parent of the agentic_rag package (i.e. "src/").
_AGENTS_DIR = os.environ.get("AGENTS_DIR", "src")

app = get_fast_api_app(
    agents_dir=_AGENTS_DIR,
    allow_origins=["*"],
    web=False,
)


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
