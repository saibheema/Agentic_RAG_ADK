"""
Odoo XML-RPC client with in-process auth token cache.

Targets the Odoo 16/17 JSON-RPC / XML-RPC v2 API:
  - /xmlrpc/2/common  → authenticate, server_version
  - /xmlrpc/2/object  → execute_kw (search_read, fields_get, create, …)

Performance strategy (millisecond requirement):
  - Auth UID is cached in-process; re-auth only on AuthError (uid=-1 or fault).
  - All search_read calls pass explicit `fields` to minimise JSON payload.
  - Module-level singleton (_client_cache) avoids repeated construction.
"""

from __future__ import annotations

import logging
import os
import threading
import xmlrpc.client
from typing import Any

_log = logging.getLogger(__name__)

# ── Singleton cache (keyed by (url, db, user)) ──────────────────────────────

_client_lock = threading.Lock()
_client_cache: dict[tuple, "OdooClient"] = {}


def get_odoo_client(
    url: str | None = None,
    db: str | None = None,
    user: str | None = None,
    password: str | None = None,
) -> "OdooClient":
    """Return (or create) the module-level singleton OdooClient.

    Falls back to env vars / Secret Manager when keyword args are omitted.
    """
    resolved_url = url or os.environ.get("ODOO_URL", "").rstrip("/")
    resolved_db = db or os.environ.get("ODOO_DB", "")
    resolved_user = user or os.environ.get("ODOO_USER", "")
    resolved_password = password or _resolve_odoo_password()

    if not resolved_url:
        # Try connections.json
        try:
            from agentic_rag.connections import get_connection, resolve_password

            conn = get_connection("odoo_sandbox")
            if conn:
                resolved_url = conn.get("url", "").rstrip("/")
                resolved_db = conn.get("database", resolved_db)
                resolved_user = conn.get("user", resolved_user)
                resolved_password = resolve_password(conn) or resolved_password
        except Exception:
            pass

    key = (resolved_url, resolved_db, resolved_user)
    with _client_lock:
        if key not in _client_cache:
            _client_cache[key] = OdooClient(
                resolved_url, resolved_db, resolved_user, resolved_password
            )
        return _client_cache[key]


def _resolve_odoo_password() -> str:
    secret_name = os.environ.get("ODOO_PASSWORD_SECRET", "").strip()
    if secret_name:
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            resp = client.access_secret_version(request={"name": secret_name})
            return resp.payload.data.decode("utf-8").strip()
        except Exception as exc:
            _log.warning("Odoo secret lookup failed: %s", exc)
    return os.environ.get("ODOO_PASSWORD", "")


# ── Client ───────────────────────────────────────────────────────────────────


class OdooClient:
    """Thread-safe Odoo XML-RPC client with cached UID."""

    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        self._url = url
        self._db = db
        self._user = user
        self._password = password
        self._uid: int | None = None
        self._uid_lock = threading.Lock()

        common_url = f"{url}/xmlrpc/2/common"
        object_url = f"{url}/xmlrpc/2/object"
        # allow_none=True prevents crashes on Odoo fields that return None
        self._common = xmlrpc.client.ServerProxy(common_url, allow_none=True)
        self._object = xmlrpc.client.ServerProxy(object_url, allow_none=True)

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _auth(self, force: bool = False) -> int:
        """Return cached UID, re-authenticating if needed."""
        with self._uid_lock:
            if self._uid is None or force:
                uid = self._common.authenticate(
                    self._db, self._user, self._password, {}
                )
                if not uid:
                    raise PermissionError(
                        f"Odoo auth failed for user {self._user!r} on db {self._db!r}"
                    )
                self._uid = int(uid)
                _log.debug("Odoo auth OK uid=%s", self._uid)
            return self._uid

    # ── Core execute_kw wrapper ───────────────────────────────────────────────

    def execute(
        self,
        model: str,
        method: str,
        args: list | None = None,
        kwargs: dict | None = None,
    ) -> Any:
        """Call execute_kw, retrying once on auth failure."""
        args = args or []
        kwargs = kwargs or {}
        try:
            uid = self._auth()
            return self._object.execute_kw(
                self._db, uid, self._password, model, method, args, kwargs
            )
        except xmlrpc.client.Fault as exc:
            # Odoo raises AccessDenied as a fault; try re-auth once
            if "AccessDenied" in str(exc) or "Access Denied" in str(exc):
                _log.warning("Odoo AccessDenied — re-authenticating")
                uid = self._auth(force=True)
                return self._object.execute_kw(
                    self._db, uid, self._password, model, method, args, kwargs
                )
            raise

    # ── High-level helpers ────────────────────────────────────────────────────

    def search_read(
        self,
        model: str,
        domain: list | None = None,
        fields: list[str] | None = None,
        limit: int = 80,
        offset: int = 0,
        order: str = "",
    ) -> list[dict[str, Any]]:
        """Return records matching `domain`.

        Always passes `fields` to keep payloads minimal.
        """
        kw: dict[str, Any] = {"limit": limit, "offset": offset}
        if fields:
            kw["fields"] = fields
        if order:
            kw["order"] = order
        return self.execute(model, "search_read", [domain or []], kw)

    def search_count(self, model: str, domain: list | None = None) -> int:
        return int(self.execute(model, "search_count", [domain or []]))

    def fields_get(
        self, model: str, attributes: list[str] | None = None
    ) -> dict[str, Any]:
        """Return field metadata for a model.

        Requests only ['string', 'type', 'required', 'readonly'] by default
        so the response stays small.
        """
        kw: dict[str, Any] = {
            "attributes": attributes or ["string", "type", "required", "readonly"]
        }
        return self.execute(model, "fields_get", [], kw)

    def read(
        self, model: str, ids: list[int], fields: list[str] | None = None
    ) -> list[dict[str, Any]]:
        kw: dict[str, Any] = {}
        if fields:
            kw["fields"] = fields
        return self.execute(model, "read", [ids], kw)

    def read_group(
        self,
        model: str,
        domain: list,
        fields: list,
        groupby: list,
        limit: int = 80,
        orderby: str = "",
    ) -> list[dict[str, Any]]:
        """Group-by aggregation via Odoo read_group.

        Args:
            model:   Odoo model technical name.
            domain:  Filter criteria (same syntax as search_read).
            fields:  Fields to aggregate, e.g. ['amount_total:sum', 'id:count'].
                     Include the group-by fields as plain names too.
            groupby: Fields to group by, e.g. ['partner_id', 'state'].
            limit:   Max number of groups to return.
            orderby: Sort expression, e.g. 'amount_total_sum desc'.
        """
        kw: dict[str, Any] = {"lazy": False}
        if limit:
            kw["limit"] = limit
        if orderby:
            kw["orderby"] = orderby
        return self.execute(
            model,
            "read_group",
            [domain or [], fields or [], groupby or []],
            kw,
        )

    def server_version(self) -> str:
        try:
            info = self._common.version()
            return info.get("server_version", "unknown")
        except Exception:
            return "unknown"
