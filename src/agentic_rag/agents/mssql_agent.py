"""MSSQL / YIS text-to-SQL agent (database_agent).

Owns SQL connection handling, schema discovery/caching, read-only SQL execution
with RBAC + PII masking, the Canonical Metric Dictionary instruction, and the
LlmAgent definition. Isolated so each database type stays self-contained.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import time
from decimal import Decimal
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

from agentic_rag.agents.shared import (
    _db_agent_config,
    _light_think,
    _model,
    cache_serve_callback,
    cache_store_callback,
)

_log = logging.getLogger(__name__)


# ── Database type detection ──────────────────────────────────────────────────

_DB_TYPE = os.environ.get("DB_TYPE", "postgres").strip().lower()


def _is_mssql() -> bool:
    return _DB_TYPE in ("mssql", "sqlserver", "sql_server")


def _is_mssql_type(db_type: str) -> bool:
    """Check if a db_type string indicates SQL Server (regardless of env vars)."""
    return db_type.strip().lower() in ("mssql", "sqlserver", "sql_server")


# ── PII masking ──────────────────────────────────────────────────────────────

_PII_ENABLED = os.environ.get("PII_MASKING_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
_PII_RULES = [
    r.strip()
    for r in os.environ.get("PII_DEFAULT_RULES", "phone,email").split(",")
    if r.strip()
]

_masker_cache: Any = None


def _masker():
    """Lazy-load PIIMasker singleton (avoids import cost when disabled)."""
    global _masker_cache
    if _masker_cache is not None:
        return _masker_cache
    if not _PII_ENABLED:
        _masker_cache = False
        return False
    try:
        from agentic_rag.pii_masking import PIIMasker

        use_presidio = os.environ.get("PII_USE_PRESIDIO", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        _masker_cache = PIIMasker(use_presidio=use_presidio)
        return _masker_cache
    except ImportError:
        _masker_cache = False
        return False


def _mask_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply PII masking only to contact columns (phone/email).

    Only columns whose name contains a contact keyword (email, phone, mobile,
    etc.) are masked. All other columns — names, descriptions, products,
    companies — pass through unchanged so the LLM sees real data.
    """
    m = _masker()
    if not m or not _PII_RULES:
        return rows
    try:
        from agentic_rag.pii_masking import is_contact_column
    except ImportError:
        return rows
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str) and is_contact_column(key):
                row[key] = m.mask_text(value, _PII_RULES)
    return rows


# ── Database helpers ─────────────────────────────────────────────────────────


def _resolve_db_password() -> str:
    """Return DB password from Secret Manager (DB_PASSWORD_SECRET) or env var."""
    secret_name = os.environ.get("DB_PASSWORD_SECRET", "").strip()
    if secret_name:
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            resp = client.access_secret_version(request={"name": secret_name})
            return resp.payload.data.decode("utf-8").strip()
        except Exception as exc:
            # Fall back to env var if Secret Manager fails
            import logging

            logging.getLogger(__name__).warning(
                "Secret Manager lookup failed (%s), falling back to DB_PASSWORD env var",
                exc,
            )
    return os.environ.get("DB_PASSWORD", "")


def _db_config(alias: str = "") -> dict[str, Any]:
    """Return connection config for the given alias.

    Resolution order:
      1. connections.json entry matching `alias` (or the default alias when
         `alias` is empty).
      2. Env var single-DB config (DB_HOST, DB_USER, etc.) — backward compat.
    """
    from agentic_rag.connections import default_alias as _conn_default
    from agentic_rag.connections import get_connection, resolve_password

    resolved = alias or _conn_default()
    if resolved:
        conn = get_connection(resolved)
        if conn:
            db_type = conn["db_type"].strip().lower()
            default_port = 1433 if _is_mssql_type(db_type) else 5432
            return {
                "db_type": db_type,
                "user": conn.get("user", ""),
                "password": resolve_password(conn),
                "database": conn.get("database", ""),
                "host": conn.get("host", "127.0.0.1"),
                "port": int(conn.get("port", default_port)),
                "max_rows": int(os.environ.get("TEXT_TO_SQL_MAX_ROWS", "200")),
                "query_timeout_ms": int(os.environ.get("TEXT_TO_SQL_QUERY_TIMEOUT_MS", "15000")),
                "allowed_tables": [
                    t.strip()
                    for t in str(conn.get("allowed_tables", "")).split(",")
                    if t.strip()
                ],
            }
        _log.warning("DB alias %r not found in connections.json — falling back to env vars", resolved)

    # ── Env var fallback (single-DB / backward compat) ───────────────────────
    default_port = 1433 if _is_mssql() else 5432
    return {
        "db_type": _DB_TYPE,
        "user": os.environ.get("DB_USER", "app_user"),
        "password": _resolve_db_password(),
        "database": os.environ.get("DB_NAME", "agentic_rag"),
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", str(default_port))),
        "max_rows": int(os.environ.get("TEXT_TO_SQL_MAX_ROWS", "200")),
        "query_timeout_ms": int(os.environ.get("TEXT_TO_SQL_QUERY_TIMEOUT_MS", "15000")),
        "allowed_tables": [
            table.strip()
            for table in os.environ.get("TEXT_TO_SQL_ALLOWED_TABLES", "").split(",")
            if table.strip()
        ],
    }


def _connect_mssql(cfg: dict[str, Any]):
    """Connect to SQL Server via pymssql."""
    import pymssql  # type: ignore[import-untyped]

    return pymssql.connect(
        server=cfg["host"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        port=cfg["port"],
        login_timeout=max(5, cfg["query_timeout_ms"] // 1000),
    )


def _connect_postgres(cfg: dict[str, Any]):
    """Connect to PostgreSQL via psycopg2."""
    import psycopg2  # type: ignore[import-untyped]

    return psycopg2.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        dbname=cfg["database"],
        connect_timeout=max(5, cfg["query_timeout_ms"] // 1000),
    )


def _connect(cfg: dict[str, Any] | None = None):
    """Open a DB connection. Pass cfg explicitly for multi-DB routing."""
    if cfg is None:
        cfg = _db_config()
    if _is_mssql_type(cfg["db_type"]):
        return _connect_mssql(cfg)
    return _connect_postgres(cfg)


_SQL_DB_TYPES = ("mssql", "sqlserver", "postgres", "postgresql")


def _sql_config(db_alias: str = "") -> dict[str, Any]:
    """Return a SQL connection config for the database_agent.

    The database_agent only speaks SQL. If the session's ``db_alias`` points at
    a non-SQL backend (e.g. the Odoo connection), opening a SQL socket to it
    would crash. In that case fall back to the default SQL connection so the
    SQL agent always has a valid SQL target. Odoo questions are handled
    separately by ``odoo_agent`` (XML-RPC) and never reach this code path.

    The returned config carries an ``_alias`` key with the effective alias used.
    """
    cfg = _db_config(db_alias)
    if cfg["db_type"] in _SQL_DB_TYPES:
        cfg["_alias"] = db_alias or "env-config"
        return cfg

    # Non-SQL alias selected (e.g. Odoo) — fall back to the default SQL
    # connection (DB_DEFAULT_ALIAS / env-config), which is the known-good,
    # reachable SQL target. Never iterate to an arbitrary connection: only the
    # default relay is routable from Cloud Run.
    default_cfg = _db_config("")
    if default_cfg["db_type"] in _SQL_DB_TYPES:
        _log.info(
            "db_alias %r is non-SQL (%s); SQL agent falling back to default SQL connection",
            db_alias,
            cfg["db_type"],
        )
        default_cfg["_alias"] = "env-config"
        return default_cfg

    # Default is also non-SQL — search configured connections for any SQL one.
    try:
        from agentic_rag.connections import list_connections

        for conn in list_connections():
            if str(conn.get("db_type", "")).strip().lower() in _SQL_DB_TYPES:
                fallback = _db_config(conn["alias"])
                fallback["_alias"] = conn["alias"]
                return fallback
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("SQL alias fallback lookup failed: %s", exc)

    # Last resort: return the default config as-is (will surface a clean error).
    default_cfg["_alias"] = "env-config"
    return default_cfg


def _to_rows(cursor) -> list[dict[str, Any]]:
    cols = [item[0] for item in cursor.description] if cursor.description else []
    out: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        converted: list[Any] = []
        for value in row:
            if isinstance(value, Decimal):
                converted.append(float(value))
            elif isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
                converted.append(value.isoformat())
            elif isinstance(value, (bytes, bytearray)):
                converted.append(value.hex())
            else:
                converted.append(value)
        out.append(dict(zip(cols, converted)))
    return out


def _classify_column_type(col_name: str, sample_values: list) -> str:  # type: ignore[type-arg]
    """Infer display type from column name and a sample of its values.

    Returns one of: currency | numeric | string | date
    as defined in the output spec (Developer Implementation Prompt).
    """
    col = col_name.lower()

    _DATE_PARTS = (
        "date", "_at", "_on", "time", "created", "updated",
        "modified", "ordered", "shipped", "delivered",
        "invoiced", "due", "period",
    )
    _CURRENCY_PARTS = (
        "price", "amount", "total", "cost", "revenue",
        "sales", "value", "balance", "charge", "fee",
        "margin", "profit", "payment", "discount", "tax", "subtotal",
    )

    if any(kw in col for kw in _DATE_PARTS):
        return "date"

    if any(kw in col for kw in _CURRENCY_PARTS):
        non_null = [v for v in sample_values if v is not None]
        if not non_null or all(isinstance(v, (int, float)) for v in non_null):
            return "currency"

    non_null = [v for v in sample_values if v is not None]
    if non_null and all(isinstance(v, (int, float)) for v in non_null):
        return "numeric"

    return "string"


def _normalized_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip()).strip()


def _validate_readonly_sql(
    sql: str, allowed_tables: list[str]
) -> tuple[bool, str]:
    normalized = _normalized_sql(sql).lower()

    if not normalized:
        return False, "SQL is empty"

    if not (normalized.startswith("select ") or normalized.startswith("with ")):
        return False, "Only SELECT/WITH read-only queries are allowed"

    blocked_tokens = [
        " insert ",
        " update ",
        " delete ",
        " drop ",
        " alter ",
        " create ",
        " truncate ",
        " grant ",
        " revoke ",
        " merge ",
        " call ",
        " execute ",
        " copy ",
    ]

    padded = f" {normalized} "
    for token in blocked_tokens:
        if token in padded:
            return False, f"Blocked keyword detected: {token.strip()}"

    if ";" in normalized[:-1]:
        return False, "Multiple SQL statements are not allowed"

    # Block system catalog access for both PostgreSQL and SQL Server
    if " information_schema." in padded:
        return False, "System schemas are blocked"
    if " pg_catalog." in padded:
        return False, "System schemas are blocked (pg_catalog)"
    if " sys." in padded:
        return False, "System schemas are blocked (sys)"

    # Block STRING_AGG(DISTINCT ...) — unsupported in SQL Server (T-SQL)
    if re.search(r"\bstring_agg\s*\(\s*distinct\b", normalized):
        return False, "Forbidden syntax: STRING_AGG(DISTINCT ...) is not supported"

    return True, "ok"


def _inject_limit_if_missing(sql: str, max_rows: int, db_type: str = "") -> str:
    actual_type = db_type or _DB_TYPE
    normalized = _normalized_sql(sql).lower()

    # Never inject a row limit on aggregate queries:
    # • GROUP BY queries: TOP N would silently drop groups, giving wrong totals.
    # • Scalar aggregates (SUM/COUNT/AVG/MIN/MAX with no GROUP BY): always
    #   return exactly 1 row, so a limit is meaningless.
    # In both cases the LLM should control the result shape explicitly.
    #
    # Scope these checks to the *outer* SELECT only — CTEs often contain GROUP BY
    # internally while the outer projection is a plain SELECT that does need TOP/LIMIT.
    # rfind('select') on the normalised string reliably finds the outermost SELECT
    # because CTE/subquery SELECTs always appear earlier in the string.
    _last_sel = normalized.rfind('select')
    _outer = normalized[_last_sel:] if _last_sel != -1 else normalized
    _has_group_by = bool(re.search(r'\bgroup\s+by\b', _outer))
    _has_scalar_agg = bool(re.search(r'\b(SUM|COUNT|AVG|MIN|MAX)\s*\(', _outer))
    if _has_group_by or _has_scalar_agg:
        return sql

    if _is_mssql_type(actual_type):
        # SQL Server uses TOP N — syntax: SELECT [DISTINCT] TOP N ...
        if " top " in f" {normalized} ":
            return sql
        sql = sql.rstrip().rstrip(";")
        # DISTINCT must come before TOP in T-SQL.
        # Correct: SELECT DISTINCT TOP 100 col ...
        # Wrong:   SELECT TOP 100 DISTINCT col ...  ← causes syntax error
        if re.search(r"(?i)\bSELECT\s+DISTINCT\b", sql):
            return re.sub(
                r"(?i)\bSELECT\s+DISTINCT\b",
                f"SELECT DISTINCT TOP {max_rows}",
                sql,
                count=1,
            )
        # Plain SELECT or CTE (WITH ... SELECT ...).
        # Find the LAST SELECT (the outermost projection in a CTE) and inject TOP N.
        # Using rfind on lowercased sql is simpler and more reliable than a
        # negative-lookahead regex which fails on multi-line CTEs.
        lowered = sql.lower()
        last_select_pos = lowered.rfind('select')
        if last_select_pos == -1:
            return sql  # no SELECT found — return as-is
        after_select = sql[last_select_pos + len('select'):]
        return sql[:last_select_pos] + f"SELECT TOP {max_rows}" + after_select

    # PostgreSQL uses LIMIT
    if " limit " in f" {normalized} ":
        return sql
    sql = sql.rstrip().rstrip(";")
    return f"{sql} LIMIT {max_rows}"


# ── Database Agent tools ─────────────────────────────────────────────────────

# Schema cache: { cache_key -> {"data": {...}, "fetched_at": float} }
# Expires after SCHEMA_CACHE_TTL_SECONDS (default: 24 h). Set
# SCHEMA_CACHE_TTL_SECONDS=0 in the environment to disable caching.
_schema_cache: dict[str, dict[str, Any]] = {}
_SCHEMA_CACHE_TTL = int(os.environ.get("SCHEMA_CACHE_TTL_SECONDS", str(24 * 3600)))


def _schema_cache_key(cfg: dict[str, Any]) -> str:
    # "*" means auto-discover all tables (TEXT_TO_SQL_ALLOWED_TABLES not set)
    tables = ",".join(sorted(cfg["allowed_tables"])) if cfg["allowed_tables"] else "*"
    return f"{cfg['db_type']}|{cfg['host']}:{cfg['port']}|{cfg['database']}|{tables}"


def _discover_all_tables(cur, db_type: str = "") -> list[str]:
    """Return all user table names from the connected database.

    Used when TEXT_TO_SQL_ALLOWED_TABLES / connections.json allowed_tables is
    empty — the agent discovers the full schema automatically.
    """
    actual_type = db_type or _DB_TYPE
    if _is_mssql_type(actual_type):
        cur.execute(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE IN ('BASE TABLE', 'VIEW') AND TABLE_SCHEMA = 'dbo' "
            "ORDER BY TABLE_NAME"
        )
    else:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type IN ('BASE TABLE', 'VIEW') "
            "ORDER BY table_name"
        )
    return [row[0] for row in cur.fetchall()]


def get_schema_metadata(tool_context: ToolContext) -> dict[str, Any]:
    """Return table/column schema metadata for the active database.

    The active database is determined by the session's db_alias state key,
    set when the session was created from the UI DB selector.
    When TEXT_TO_SQL_ALLOWED_TABLES / connections.json allowed_tables is set,
    only those tables are included. When empty, ALL tables are auto-discovered.

    Results are cached in-process for SCHEMA_CACHE_TTL_SECONDS (default 24 h)
    to avoid a DB round-trip on every agent invocation.
    """
    db_alias = tool_context.state.get("db_alias", "")
    cfg = _sql_config(db_alias)
    db_alias = cfg["_alias"]
    allowed_tables = cfg["allowed_tables"]  # empty list = auto-discover

    # ── Cache check ──────────────────────────────────────────────────────────
    cache_key = _schema_cache_key(cfg)
    if _SCHEMA_CACHE_TTL > 0:
        entry = _schema_cache.get(cache_key)
        if entry and (time.time() - entry["fetched_at"]) < _SCHEMA_CACHE_TTL:
            age_h = (time.time() - entry["fetched_at"]) / 3600
            _log.debug("Schema cache hit (age %.1fh, TTL %dh)", age_h, _SCHEMA_CACHE_TTL // 3600)
            # Always inject a fresh 'today' and live access_context — never serve stale values
            cached = dict(entry["data"])
            cached["today"] = datetime.date.today().isoformat()
            cached["access_context"] = {
                "user_name": tool_context.state.get("user_name", ""),
                "role_name": tool_context.state.get("role_name", ""),
                "replevel": int(tool_context.state.get("replevel", 1)),
                "salesperson_id": str(tool_context.state.get("salesperson_id", "")),
            }
            # Inject cross-session topic context set by the UI on proactive reset.
            # Empty string on fresh sessions — the agent ignores this key when absent.
            _session_ctx = tool_context.state.get("session_context", "")
            if _session_ctx:
                cached["previous_session_context"] = _session_ctx
            return cached  # type: ignore[return-value]

    conn = None
    try:
        conn = _connect(cfg)  # MUST pass cfg so the right DB alias is used
        cur = conn.cursor()

        # ── Auto-discover tables when none are configured ─────────────────
        if not allowed_tables:
            allowed_tables = _discover_all_tables(cur, cfg["db_type"])
            _log.info(
                "Auto-discovered %d tables from %s [%s]",
                len(allowed_tables),
                cfg["database"],
                db_alias or "env-config",
            )
            if not allowed_tables:
                return {"tables": [], "note": "No user tables found in database"}

        placeholders = ", ".join(["%s"] * len(allowed_tables))

        if _is_mssql_type(cfg["db_type"]):
            # SQL Server: default schema is 'dbo', use %s placeholders (pymssql)
            sql = f"""
            SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name, DATA_TYPE AS data_type
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'dbo'
              AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """
        else:
            # PostgreSQL: default schema is 'public'
            sql = f"""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name IN ({placeholders})
            ORDER BY table_name, ordinal_position
            """

        cur.execute(sql, tuple(allowed_tables))
        rows = _to_rows(cur)

        tables: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            table_name = row["table_name"]
            tables.setdefault(table_name, []).append(
                {
                    "column": str(row["column_name"]),
                    "data_type": str(row["data_type"]),
                }
            )

        # ── Sample rows per table — single batched UNION ALL per table ────────────
        # Fetch 2 sample rows per table in one round-trip using UNION ALL.
        samples: dict[str, list[dict[str, Any]]] = {tname: [] for tname in tables}
        for tname in list(tables.keys()):
            try:
                if _is_mssql_type(cfg["db_type"]):
                    cur.execute(
                        f"SELECT TOP 2 * FROM [dbo].[{tname}] WITH (NOLOCK)"
                    )
                else:
                    cur.execute(f'SELECT * FROM "{tname}" LIMIT 2')
                samples[tname] = _mask_rows(_to_rows(cur))  # mask PII in sample rows too
            except Exception:
                samples[tname] = []

        # Human-readable dialect label so the LLM activates its full syntax
        # knowledge rather than pattern-matching on our internal code names.
        _DIALECT_LABEL = {
            "mssql": "Microsoft SQL Server (T-SQL)",
            "sqlserver": "Microsoft SQL Server (T-SQL)",
            "sql_server": "Microsoft SQL Server (T-SQL)",
            "postgres": "PostgreSQL",
            "postgresql": "PostgreSQL",
        }
        sql_dialect = _DIALECT_LABEL.get(cfg["db_type"].lower(), cfg["db_type"])

        # NOTE: 'today' is intentionally NOT stored in the cache. It is
        # injected fresh on every return so date-relative queries always use
        # the actual current date, never a stale cached date.
        result: dict[str, Any] = {
            "tables": [
                {
                    "table": table_name,
                    "columns": columns,
                    "sample_rows": samples.get(table_name, []),
                }
                for table_name, columns in tables.items()
            ],
            "active_db": db_alias or "env-config",
            "db_type": sql_dialect,
        }

        # ── Populate cache (without today) ───────────────────────────────────
        if _SCHEMA_CACHE_TTL > 0:
            _schema_cache[cache_key] = {"data": result, "fetched_at": time.time()}
            _log.debug(
                "Schema cached for %s [%s] (%d tables, TTL %dh)",
                cfg["database"],
                db_alias or "env-config",
                len(result["tables"]),
                _SCHEMA_CACHE_TTL // 3600,
            )

        result["today"] = datetime.date.today().isoformat()
        result["access_context"] = {
            "user_name": tool_context.state.get("user_name", ""),
            "role_name": tool_context.state.get("role_name", ""),
            "replevel": int(tool_context.state.get("replevel", 1)),
            "salesperson_id": str(tool_context.state.get("salesperson_id", "")),
        }
        # Inject cross-session topic context set by the UI on proactive reset.
        # Empty string on fresh sessions — the agent ignores this key when absent.
        _session_ctx = tool_context.state.get("session_context", "")
        if _session_ctx:
            result["previous_session_context"] = _session_ctx
        return result
    except Exception as exc:
        _log.warning("Schema metadata fetch failed for [%s]: %s", db_alias or "env-config", exc)
        return {"tables": [], "error": str(exc)}
    finally:
        if conn is not None:
            conn.close()


def _check_rbac_access(sql: str, replevel: int, salesperson_id: str) -> tuple[bool, str]:
    """Enforce row-level access control on auto-generated SQL.

    Returns (allowed: bool, error_message: str).
    Pure function — no I/O, fully unit-testable.

    Rules:
      replevel 1 (Internal) — full access, no restriction.
      replevel 3 (Manager)  — SQL must contain the manager's salesperson_id
                              (expected to appear in a LIKE '{id}%' clause).
      replevel 5 (Salesperson) — SQL must contain the exact salesperson_id
                              (expected in WHERE salesperson_id = '{id}').
    """
    spid = salesperson_id.strip()
    if replevel == 5 and spid:
        if spid.lower() not in sql.lower():
            return False, (
                f"Access control: salesperson-level (replevel=5) queries must "
                f"include a filter for salesperson_id = '{spid}'. "
                f"Add WHERE salesperson_id = '{spid}' to the query and retry."
            )
    elif replevel == 3 and spid:
        if spid.lower() not in sql.lower():
            return False, (
                f"Access control: manager-level (replevel=3) queries must "
                f"include a filter for salesperson_id LIKE '{spid}%'. "
                f"Add WHERE salesperson_id LIKE '{spid}%' to the query and retry."
            )
    return True, "ok"


def run_readonly_sql(sql: str, tool_context: ToolContext) -> dict[str, Any]:
    """Execute LLM-generated read-only SQL against the active database.

    The active database is determined by the session's db_alias state key.
    Only SELECT/WITH queries are allowed; all writes are blocked.
    """
    db_alias = tool_context.state.get("db_alias", "")
    cfg = _sql_config(db_alias)
    db_alias = cfg["_alias"]
    allowed_tables = cfg["allowed_tables"]
    max_rows = max(1, cfg["max_rows"])
    timeout_ms = max(1000, cfg["query_timeout_ms"])

    is_valid, reason = _validate_readonly_sql(sql, allowed_tables)
    if not is_valid:
        return {
            "ok": False,
            "error": reason,
            "allowed_tables": allowed_tables,
        }

    final_sql = _inject_limit_if_missing(sql, max_rows, cfg["db_type"])

    # ── RBAC guardrail: levels 3 and 5 must filter by their salesperson ID ────
    replevel = int(tool_context.state.get("replevel", 1))
    salesperson_id = str(tool_context.state.get("salesperson_id", "")).strip()
    rbac_ok, rbac_error = _check_rbac_access(final_sql, replevel, salesperson_id)
    if not rbac_ok:
        return {"ok": False, "error": rbac_error}

    conn = None
    try:
        conn = _connect(cfg)
        cur = conn.cursor()
        if _is_mssql_type(cfg["db_type"]):
            # SQL Server: no SET statement_timeout; rely on login_timeout
            pass
        else:
            cur.execute(f"SET statement_timeout TO {timeout_ms}")
        cur.execute(final_sql)
        rows = _to_rows(cur)
        columns = (
            [item[0] for item in cur.description] if cur.description else []
        )

        # Apply PII masking to results before returning to the LLM
        rows = _mask_rows(rows)

        # Build columns_meta for structured JSON output
        columns_meta = [
            {
                "key": col,
                "header": " ".join(w.capitalize() for w in col.replace("_", " ").split()),
                "type": _classify_column_type(col, [row.get(col) for row in rows[:5]]),
            }
            for col in columns
        ]

        return {
            "ok": True,
            "active_db": db_alias or "env-config",
            "sql_executed": _normalized_sql(final_sql),
            "row_count": len(rows),
            "columns": columns,
            "columns_meta": columns_meta,
            "rows": rows,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "sql_executed": _normalized_sql(final_sql),
        }
    finally:
        if conn is not None:
            conn.close()


def prewarm_schema_cache() -> None:
    """Pre-warm the schema cache for all configured connections.

    Call this once at server startup (e.g. in a background thread) so the
    first user query hits cache instead of triggering a cold DB round-trip.
    """
    try:
        from agentic_rag.connections import list_connections
        aliases = [c["alias"] for c in list_connections()]
    except Exception:
        aliases = [""]

    class _FakeCtx:
        """Minimal stand-in for ToolContext used only for cache prewarm."""
        def __init__(self, alias: str) -> None:
            self.state = {"db_alias": alias}

    for alias in aliases:
        try:
            _log.info("Prewarming schema cache for alias=%r", alias)
            get_schema_metadata(_FakeCtx(alias))  # type: ignore[arg-type]
            _log.info("Schema cache warm for alias=%r", alias)
        except Exception as exc:
            _log.warning("Schema prewarm failed for alias=%r: %s", alias, exc)


def _schema_for_state(state: Any) -> dict[str, Any]:
    """Fetch schema metadata for the given session state from the warm cache.

    Reuses get_schema_metadata (in-process cache, ~0ms when warm) so the schema
    can be embedded directly into the prompt — the agent no longer spends an LLM
    turn calling get_schema_metadata, nor a turn calling rewrite_query.
    """
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.state = state  # MappingProxyType / dict — get_schema_metadata only reads
    try:
        return get_schema_metadata(ctx)  # type: ignore[arg-type]
    except Exception as exc:  # never break prompt build on a schema hiccup
        _log.warning("instruction schema fetch failed: %s", exc)
        return {}


def _database_instruction(ctx) -> str:
    """Dynamic instruction for database_agent.

    Embeds the live session context, today's date, and the FULL database schema
    (served from the warm in-process cache) directly into the prompt. This lets
    the agent generate SQL in a single pass — it no longer spends one LLM turn
    on rewrite_query and another on get_schema_metadata. Because the instruction
    is a callable, ADK skips {placeholder} injection, so all values are embedded
    literally here.
    """
    state = ctx.state
    user_name = state.get("user_name", "") or "User"
    role_name = state.get("role_name", "") or "Internal"
    try:
        replevel = int(state.get("replevel", 1) or 1)
    except (TypeError, ValueError):
        replevel = 1
    salesperson_id = str(state.get("salesperson_id", "") or "")

    schema = _schema_for_state(state)
    today = schema.get("today") or datetime.date.today().isoformat()
    db_type = schema.get("db_type") or "Microsoft SQL Server (T-SQL)"
    schema_payload = {
        "active_db": schema.get("active_db", ""),
        "db_type": db_type,
        "tables": schema.get("tables", []),
    }
    schema_json = json.dumps(schema_payload, default=str, ensure_ascii=False)

    return (
        "You are Ayra, a professional sales data assistant.\n"
        "You convert natural language into SQL queries and return structured JSON.\n\n"

        "## SESSION CONTEXT\n"
        f"User: {user_name} | Role: {role_name} | Access level: {replevel} | "
        f"Salesperson ID: {salesperson_id}\n"
        f"Today's date: {today}\n"
        f"Database engine: {db_type}\n\n"

        "## DATABASE SCHEMA — already loaded, DO NOT request it again\n"
        "The complete schema for the active database (tables, columns, and sample "
        "rows) is provided below as JSON. Use it directly. NEVER call any schema "
        "tool — there is none.\n"
        f"{schema_json}\n\n"

        "## WORKFLOW — ALWAYS FOLLOW THIS EXACT ORDER\n"
        "Step 1. Interpret the user's LATEST message directly. Normalise IDs and "
        "terminology yourself, and resolve follow-up references ('those customers', "
        "'that salesperson', 'the same period', 'show me the top 5') from the "
        "conversation history.\n"
        "Step 2. Identify the correct SQL view from VIEW ROUTING below.\n"
        "Step 3. Write ONE read-only SELECT query applying ACCESS CONTROL, DISPLAY "
        "RULES, and RESULT LIMITS.\n"
        "Step 4. Call run_readonly_sql EXACTLY ONCE.\n"
        "Step 5. Return ONLY the JSON response described in OUTPUT FORMAT.\n\n"

        "## ACCESS CONTROL — MANDATORY\n"
        "The SESSION CONTEXT above gives your access level and salesperson_id for this request.\n"
        f"  replevel == 5 (Salesperson): every query MUST have  WHERE salesperson_id = '{salesperson_id}'.\n"
        f"  replevel == 3 (Manager):     every query MUST have  WHERE salesperson_id LIKE '{salesperson_id}%'.\n"
        "                               For team-only reports exclude the manager's own row.\n"
        "  replevel == 1 (Internal):    no salesperson filter — full access.\n\n"

        "## SQL VIEW ROUTING — MANDATORY\n"
        "These are the ONLY 9 views that exist. Use ONLY these exact names. "
        "DO NOT invent, guess, or use any other view or table name under any circumstances.\n"
        "  product_inquiry          → dbo.vw_wholesale_product_catalog\n"
        "  order_summary            → dbo.vw_salesperson_orders_summary         (salesperson column = salesperson_id)\n"
        "  order_details            → dbo.vw_salesperson_orders_detail\n"
        "  order_shipment_summary   → dbo.vw_salesperson_pending_shipments_summary\n"
        "  order_shipment_details   → dbo.vw_salesperson_pending_shipments_detail\n"
        "  invoice_summary          → dbo.vw_salesperson_invoices_summary\n"
        "  invoice_details          → dbo.vw_salesperson_invoices_detail\n"
        "  promotion_inquiry        → dbo.vw_CurrentSpecials\n"
        "  account_details          → dbo.vw_salesperson_customers_master\n"
        "If a query spans multiple intents, pick the most specific view. "
        "If the DATABASE SCHEMA above does not list a view from this list, "
        "still use the correct view name from this list — never substitute another view.\n\n"

        "## CANONICAL METRIC DICTIONARY — AUTHORITATIVE, NON-NEGOTIABLE\n"
        "These are the ONLY correct definitions for the most common business "
        "metrics. When a request matches one of these, you MUST use the EXACT "
        "view, aggregate column, GROUP BY column, and filter shown — do NOT "
        "improvise a different view, column, or date filter. This guarantees the "
        "same question always returns the same numbers.\n\n"

        "  • Salesperson revenue / sales 'this year' (a.k.a. total revenue, "
        "top/best salespersons, sales performance this year):\n"
        "      view    = dbo.vw_salesperson_customers_master\n"
        "      metric  = SUM(current_year_total_sales_amount)\n"
        "      groupby = salesperson_name\n"
        "      order   = ORDER BY SUM(current_year_total_sales_amount) DESC\n"
        "      NOTE: current_year_total_sales_amount is ALREADY scoped to the "
        "current year — DO NOT add any invoice_date / date filter.\n"
        "  • Salesperson revenue / sales 'last year':\n"
        "      same view + groupby, metric = SUM(last_year_total_sales_amount). No date filter.\n"
        "  • Salesperson sales 'year to date' / 'YTD':\n"
        "      same view + groupby, metric = SUM(current_year_to_date_sales_amount) "
        "(last year YTD = SUM(last_year_to_date_sales_amount)). No date filter.\n"
        "  • Customer revenue / sales 'this year' (top/best customers by sales):\n"
        "      view    = dbo.vw_salesperson_customers_master\n"
        "      metric  = SUM(current_year_total_sales_amount)\n"
        "      groupby = customer_business_name\n"
        "      (last year = SUM(last_year_total_sales_amount)). No date filter.\n"
        "  • Invoiced revenue over an EXPLICIT date range, or when the user says "
        "'invoiced'/'invoices' with specific dates:\n"
        "      view    = dbo.vw_salesperson_invoices_summary\n"
        "      metric  = SUM(total_invoice_amount)\n"
        "      groupby = salesperson_name (or billing_customer_name for customers)\n"
        "      filter  = invoice_date within the requested range.\n"
        "  • Order value / bookings (total/open orders):\n"
        "      view    = dbo.vw_salesperson_orders_summary\n"
        "      metric  = SUM(total_order_value)  (open orders = SUM(open_order_value))\n"
        "      groupby = salesperson_name (or customer_business_name)\n"
        "      filter  = order_date within the requested range when a range is given.\n"
        "  • Product revenue 'this year' (top/best-selling products by revenue):\n"
        "      view    = dbo.vw_wholesale_product_catalog\n"
        "      metric  = current_year_revenue  (already per-product; ORDER BY DESC, no GROUP BY)\n"
        "      label   = product_name_description.\n"
        "  • Most profitable products / 'best items' / 'top margin':\n"
        "      view    = dbo.vw_wholesale_product_catalog\n"
        "      order   = ORDER BY profit_margin_percent DESC (or profit_margin_rank ASC)\n"
        "      label   = product_name_description.\n"
        "If the user's request does NOT match any entry above, fall back to the "
        "VIEW ROUTING list and the actual columns in the DATABASE SCHEMA.\n\n"

        "## SQL RULES\n"
        "- SELECT statements ONLY. Never INSERT / UPDATE / DELETE / DROP / ALTER / TRUNCATE.\n"
        "- Never SELECT *. Always list columns explicitly.\n"
        "- Never use STRING_AGG(DISTINCT ...) — unsupported in SQL Server.\n"
        "- Use LIKE '%name%' for fuzzy name/company matching.\n"
        "- One query per run_readonly_sql call.\n"
        "- Database engine: Microsoft SQL Server 2018 (T-SQL).\n\n"

        "## RESULT LIMITS\n"
        "- List queries (row-level data): SELECT TOP 5 by default; respect user's number, hard cap 200.\n"
        "- Aggregate queries (GROUP BY or SUM/COUNT/AVG/MIN/MAX): NO TOP / LIMIT.\n\n"

        "## DATE RULES\n"
        "Always derive dates from the 'Today's date' shown in SESSION CONTEXT — never use training-data dates.\n"
        "  'this year'  → YEAR(col) = YEAR(GETDATE())     (e.g. 2026)\n"
        "  'last year'  → YEAR(col) = YEAR(GETDATE())-1   (e.g. 2025)\n"
        "  'this month' → MONTH+YEAR of today\n"
        "  'last month' → previous calendar month\n"
        "  'today'      → CAST(col AS DATE) = CAST(GETDATE() AS DATE)\n"
        "When the user gives an explicit year, use it literally. Do not recompute.\n"
        "IMPORTANT: When a metric in the CANONICAL METRIC DICTIONARY is already "
        "year-scoped (current_year_*/last_year_* columns), use that column and add "
        "NO date filter — the dictionary overrides these date rules.\n\n"

        "## DISPLAY RULES\n"
        "- ALWAYS include the customer business name and product name in results.\n"
        "- NEVER include customer account numbers (e.g. A024874) unless explicitly requested.\n"
        "- NEVER include product item numbers (numeric codes) unless explicitly requested.\n\n"

        "## BUSINESS LOGIC\n"
        "- 'top items' / 'best items' / 'best-selling items' and 'top customers' / "
        "'best clients' / 'top salespersons' are DEFINED in the CANONICAL METRIC "
        "DICTIONARY above — use those exact definitions.\n"
        "- RFM analysis: requires Recency, Frequency, and Monetary columns.\n\n"

        "## FOLLOW-UP QUESTIONS\n"
        "Use conversation history to resolve references like 'those customers', "
        "'that salesperson', 'the same period', 'show me the top 5'.\n"
        "Build a new SQL query that incorporates the user's filter or change.\n\n"

        "## ERROR HANDLING\n"
        "- If run_readonly_sql returns ok=false: fix the SQL and retry ONCE.\n"
        "- If it fails a second time: put the error message in the JSON 'error' field.\n"
        "- If it returns 'DATABASE_CONNECTION_ERROR': set error='DATABASE_CONNECTION_ERROR' and stop.\n\n"

        "## OUTPUT FORMAT — CRITICAL\n"
        "Return ONLY a raw JSON object. NO markdown code fences (no ```json). "
        "NO text before or after. Your entire response MUST start with { and end with }.\n\n"
        "Normal data query:\n"
        "{\n"
        '  "sql_query": "SELECT TOP 5 ... FROM dbo.vw_... WHERE ...",\n'
        '  "columns_meta": [{"key": "col_name", "header": "Col Name", "type": "currency|numeric|string|date"}],\n'
        '  "results": [ ... array of row objects from run_readonly_sql ... ],\n'
        '  "columns": null,\n'
        '  "insights": ["Insight 1.", "Insight 2.", "Insight 3.", "Insight 4."],\n'
        '  "summary": "2-4 sentence explanation of the results.",\n'
        '  "greetings": null,\n'
        '  "error": null\n'
        "}\n\n"
        "INSIGHTS RULE: For every data query that returns results you MUST include exactly 3-4 insights. "
        "Never return an empty insights array when results are present. "
        "Insights must explain patterns, highlight top/bottom performers, flag anomalies, or give business context.\n\n"

        "## SPECIAL CASES — return exact 8-field JSON for each\n\n"

        "Greeting (hello / hi / good morning / good afternoon):\n"
        "{\n"
        '  "sql_query": null,\n'
        '  "columns_meta": [],\n'
        '  "results": [],\n'
        '  "columns": null,\n'
        '  "insights": [],\n'
        '  "summary": null,\n'
        '  "greetings": "Hello! I\'m Ayra, your sales data assistant. Ask me about orders, customers, products, invoices, or sales performance.",\n'
        '  "error": null\n'
        "}\n\n"

        "Capabilities question (what can you do / what do you know / help):\n"
        "{\n"
        '  "sql_query": null,\n'
        '  "columns_meta": [],\n'
        '  "results": [],\n'
        '  "columns": null,\n'
        '  "insights": [],\n'
        '  "summary": "I\'m Ayra, a sales data assistant. I can analyse orders, customers, products, invoices, sales performance, pending shipments, promotions, and RFM customer segmentation. Ask me anything about your sales data.",\n'
        '  "greetings": null,\n'
        '  "error": null\n'
        "}\n\n"

        "Irrelevant question (not related to sales/business data):\n"
        "{\n"
        '  "sql_query": null,\n'
        '  "columns_meta": [],\n'
        '  "results": [],\n'
        '  "columns": null,\n'
        '  "insights": [],\n'
        '  "summary": null,\n'
        '  "greetings": null,\n'
        '  "error": "This assistant focuses on sales and business data analysis. Please ask a question related to orders, customers, products, or sales."\n'
        "}\n\n"

        "Database / SQL error:\n"
        "{\n"
        '  "sql_query": "<the SQL that was attempted>",\n'
        '  "columns_meta": [],\n'
        '  "results": [],\n'
        '  "columns": null,\n'
        '  "insights": [],\n'
        '  "summary": null,\n'
        '  "greetings": null,\n'
        '  "error": "<exact error message from run_readonly_sql>"\n'
        "}\n"
    )


database_agent = LlmAgent(
    name="database_agent",
    model=_model,
    planner=_light_think,
    generate_content_config=_db_agent_config,
    description=(
        "Specialist for structured data questions. Handles anything about "
        "orders, customers, products, sales, counts, totals, rankings, "
        "averages, or any question answerable with SQL. Also handles greetings."
    ),
    instruction=_database_instruction,
    before_agent_callback=cache_serve_callback,
    after_model_callback=cache_store_callback,
    tools=[
        FunctionTool(run_readonly_sql),
    ],
)
