"""Multi-database connection registry.

Reads connections.json (path controlled by DB_CONNECTIONS_FILE env var).
Falls back to single-DB env var config if file not found or alias not matched.

Password resolution order per connection entry:
  1. ``password_secret`` — Secret Manager resource path
     e.g. "projects/my-proj/secrets/yisbeta-pwd/versions/latest"
  2. ``password_env``    — name of an env var holding the password
     e.g. "YISBETA_PASSWORD"  → reads os.environ["YISBETA_PASSWORD"]
  3. ``password``        — plaintext fallback (local dev only, never commit)

Add a new DB: add an entry to connections.json — no code changes needed.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_CONNECTIONS_FILE = Path(os.environ.get("DB_CONNECTIONS_FILE", "connections.json"))

# In-process cache: invalidated only on process restart.
_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    """Load and cache connections.json."""
    global _cache
    if _cache is not None:
        return _cache
    if _CONNECTIONS_FILE.exists():
        _cache = json.loads(_CONNECTIONS_FILE.read_text(encoding="utf-8"))
        _log.info(
            "Loaded %d DB connection(s) from %s",
            len(_cache.get("connections", [])),
            _CONNECTIONS_FILE,
        )
    else:
        _cache = {"default": "", "connections": []}
    return _cache


def list_connections() -> list[dict[str, str]]:
    """Return public-safe list — alias, label, db_type only (no credentials)."""
    data = _load()
    return [
        {
            "alias": c["alias"],
            "label": c.get("label", c["alias"]),
            "db_type": c["db_type"],
            "local_only": c.get("local_only", False),
        }
        for c in data.get("connections", [])
    ]


def get_connection(alias: str) -> dict[str, Any] | None:
    """Return full connection config for the given alias, or None if not found."""
    data = _load()
    for conn in data.get("connections", []):
        if conn["alias"] == alias:
            return conn
    return None


def default_alias() -> str:
    """Return the configured default alias.

    Priority:
    1. DB_DEFAULT_ALIAS environment variable (allows Cloud Run env var override)
    2. ``default`` field in connections.json
    3. Empty string if neither is set
    """
    env_override = os.environ.get("DB_DEFAULT_ALIAS", "").strip()
    if env_override:
        return env_override
    return _load().get("default", "")


def resolve_password(conn: dict[str, Any]) -> str:
    """Resolve the password for a connection entry.

    Resolution order:
      1. password_secret  — Secret Manager resource path
      2. password_env     — name of an environment variable
      3. password         — plaintext (local dev only, never commit)
    """
    # 1. Secret Manager
    secret_path = conn.get("password_secret", "").strip()
    if secret_path:
        try:
            from google.cloud import secretmanager  # type: ignore[import-untyped]
            client = secretmanager.SecretManagerServiceClient()
            resp = client.access_secret_version(request={"name": secret_path})
            return resp.payload.data.decode("utf-8").strip()
        except Exception as exc:
            _log.warning(
                "Secret Manager lookup failed for alias %r (%s) — trying password_env",
                conn.get("alias", ""),
                exc,
            )

    # 2. Environment variable
    env_var = conn.get("password_env", "").strip()
    if env_var:
        pwd = os.environ.get(env_var, "")
        if not pwd:
            _log.warning(
                "Env var %r (password_env for alias %r) is not set or empty",
                env_var,
                conn.get("alias", ""),
            )
        return pwd

    # 3. Plaintext fallback
    plaintext = conn.get("password", "")
    if plaintext:
        _log.warning(
            "Connection %r uses a plaintext password in connections.json — "
            "use password_env or password_secret instead.",
            conn.get("alias", ""),
        )
    return plaintext
