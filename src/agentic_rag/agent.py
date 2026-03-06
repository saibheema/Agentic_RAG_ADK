"""
Multi-agent Agentic RAG — ADK entry point.

Architecture (Agentic_RAG.md):
  Router Agent (supervisor)
    ├─ Database Agent  — Text-to-SQL against PostgreSQL or SQL Server
    └─ RAG Agent       — Document retrieval via Vertex AI RAG Engine

PII masking is applied to all database query results before they reach the LLM.
Set DB_TYPE=postgres (default) or DB_TYPE=mssql to choose the backend database.
"""

from __future__ import annotations

import logging
import os
import re
import time
import datetime
from decimal import Decimal
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.planners import BuiltInPlanner
from google.adk.tools import FunctionTool, ToolContext
from google.genai import types

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

    return True, "ok"


def _inject_limit_if_missing(sql: str, max_rows: int, db_type: str = "") -> str:
    actual_type = db_type or _DB_TYPE
    normalized = _normalized_sql(sql).lower()

    # Never inject a row limit on aggregate queries:
    # • GROUP BY queries: TOP N would silently drop groups, giving wrong totals.
    # • Scalar aggregates (SUM/COUNT/AVG/MIN/MAX with no GROUP BY): always
    #   return exactly 1 row, so a limit is meaningless.
    # In both cases the LLM should control the result shape explicitly.
    _has_group_by = bool(re.search(r'\bgroup\s+by\b', normalized, re.IGNORECASE))
    _has_scalar_agg = bool(
        re.search(r'\b(SUM|COUNT|AVG|MIN|MAX)\s*\(', normalized, re.IGNORECASE)
    )
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
    cfg = _db_config(db_alias)
    allowed_tables = cfg["allowed_tables"]  # empty list = auto-discover

    # ── Cache check ──────────────────────────────────────────────────────────
    cache_key = _schema_cache_key(cfg)
    if _SCHEMA_CACHE_TTL > 0:
        entry = _schema_cache.get(cache_key)
        if entry and (time.time() - entry["fetched_at"]) < _SCHEMA_CACHE_TTL:
            age_h = (time.time() - entry["fetched_at"]) / 3600
            _log.debug("Schema cache hit (age %.1fh, TTL %dh)", age_h, _SCHEMA_CACHE_TTL // 3600)
            # Always inject a fresh 'today' — never serve a cached date
            cached = dict(entry["data"])
            cached["today"] = datetime.date.today().isoformat()
            return cached  # type: ignore[return-value]

    conn = _connect(cfg)  # MUST pass cfg so the right DB alias is used
    try:
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
        return result
    finally:
        conn.close()


def run_readonly_sql(sql: str, tool_context: ToolContext) -> dict[str, Any]:
    """Execute LLM-generated read-only SQL against the active database.

    The active database is determined by the session's db_alias state key.
    Only SELECT/WITH queries are allowed; all writes are blocked.
    """
    db_alias = tool_context.state.get("db_alias", "")
    cfg = _db_config(db_alias)
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

    conn = _connect(cfg)
    try:
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

        return {
            "ok": True,
            "active_db": db_alias or "env-config",
            "sql_executed": _normalized_sql(final_sql),
            "row_count": len(rows),
            "columns": columns,
            "rows": rows,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "sql_executed": _normalized_sql(final_sql),
        }
    finally:
        conn.close()


# ── RAG Agent tools ──────────────────────────────────────────────────────────


def retrieve_documents(query: str) -> dict[str, Any]:
    """Search the document knowledge base for relevant information.

    Uses Vertex AI RAG Engine when VERTEX_RAG_CORPUS is configured.
    Returns matching document chunks with source attribution and relevance scores.
    """
    corpus_name = os.environ.get("VERTEX_RAG_CORPUS", "").strip()

    if not corpus_name:
        return {
            "ok": False,
            "error": (
                "Document knowledge base is not configured. "
                "Set VERTEX_RAG_CORPUS=projects/PROJECT/locations/REGION"
                "/ragCorpora/CORPUS_ID to enable document search."
            ),
        }

    try:
        from vertexai.preview import rag

        response = rag.retrieval_query(
            rag_resources=[rag.RagResource(rag_corpus=corpus_name)],
            text=query,
            similarity_top_k=5,
        )
        contexts = []
        if response.contexts and response.contexts.contexts:
            for chunk in response.contexts.contexts:
                ctx = {
                    "text": chunk.text,
                    "source": getattr(chunk, "source_uri", ""),
                }
                score = getattr(chunk, "distance", None) or getattr(
                    chunk, "score", None
                )
                if score is not None:
                    ctx["score"] = round(float(score), 4)
                contexts.append(ctx)

        return {
            "ok": True,
            "query": query,
            "result_count": len(contexts),
            "results": contexts,
        }
    except ImportError:
        return {
            "ok": False,
            "error": (
                "vertexai package not installed. "
                "Add google-cloud-aiplatform to requirements.txt."
            ),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "query": query}


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


# ── Agent definitions ────────────────────────────────────────────────────────

_model = os.environ.get("AGENT_MODEL", "gemini-3.0-flash-preview")
# Lightweight model for the router — it only picks between 2 sub-agents
_router_model = os.environ.get("ROUTER_MODEL", "gemini-2.5-flash-lite")

# ── Token / thinking budget settings (tunable via env vars or Secret Manager) ─
# Update these in Cloud Run env vars (or Secret Manager) without redeploying.
_db_thinking_budget = int(os.environ.get("DB_AGENT_THINKING_BUDGET", "8192"))
_db_max_output_tokens = int(os.environ.get("DB_AGENT_MAX_OUTPUT_TOKENS", "8192"))
_rag_max_output_tokens = int(os.environ.get("RAG_AGENT_MAX_OUTPUT_TOKENS", "2048"))
_router_max_output_tokens = int(os.environ.get("ROUTER_MAX_OUTPUT_TOKENS", "256"))

# Disable extended thinking on all agents: saves 5-15s per LLM call.
# Cap output tokens to reduce generation time (SQL answers rarely exceed 1k).
_no_think = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(thinking_budget=0)
)
_light_think = BuiltInPlanner(
    thinking_config=types.ThinkingConfig(thinking_budget=_db_thinking_budget)
)
_fast_config = types.GenerateContentConfig(
    max_output_tokens=_rag_max_output_tokens,
)
# Database agent needs more headroom: thinking tokens + SQL + result table
_db_agent_config = types.GenerateContentConfig(
    max_output_tokens=_db_max_output_tokens,
)
_router_config = types.GenerateContentConfig(
    max_output_tokens=_router_max_output_tokens,
)

database_agent = LlmAgent(
    name="database_agent",
    model=_model,
    planner=_light_think,
    generate_content_config=_db_agent_config,
    description=(
        "Specialist for structured data questions. Handles anything about "
        "orders, customers, products, sales, counts, totals, rankings, "
        "averages, or any question answerable with SQL."
    ),
    instruction=(
        "You are a database SQL agent. Your SOLE purpose is to answer "
        "questions by querying a database using SQL.\n\n"
        "## STRICT RULES\n"
        "- You have EXACTLY two tools: get_schema_metadata and run_readonly_sql.\n"
        "- Your ONLY valid actions are: (a) call one of these two tools, or "
        "(b) reply with natural language text.\n"
        "- NEVER output code (Python, JavaScript, etc.), NEVER call print(), "
        "NEVER generate function calls other than these two tools.\n"
        "- The 'sql' parameter of run_readonly_sql must contain a SQL query "
        "string — never programming code.\n\n"
        "## DATE & YEAR RULES — MANDATORY\n"
        "get_schema_metadata always returns a 'today' field with the real "
        "current date (e.g. '2026-03-05').\n"
        "RULE 1 — Explicit user year: if the user says a specific year "
        "(e.g. '2025', '2024'), use that exact year literal in the SQL. "
        "Do NOT second-guess or recompute it.\n"
        "RULE 2 — Relative references: derive from 'today' — NEVER from "
        "training knowledge:\n"
        "- 'this year' / 'current year'  → YEAR(today)      e.g. 2026\n"
        "- 'last year'                   → YEAR(today) - 1  e.g. 2025\n"
        "- 'year before last'            → YEAR(today) - 2  e.g. 2024\n"
        "- 'this month'                  → MONTH+YEAR of today\n"
        "- 'last month'                  → previous calendar month\n"
        "For T-SQL: use YEAR(col), DATEPART(year, col), or date range "
        "col >= '2025-01-01' AND col < '2026-01-01'.\n\n"
        "## FOLLOW-UP QUESTIONS\n"
        "When the user sends a short follow-up like 'which are pending' or "
        "'show me the top 5', refer to the previous conversation to determine "
        "which table/columns were queried, then build a NEW SQL query that "
        "adds the user's filter or modification. Always call "
        "get_schema_metadata first if you haven't already in this turn.\n\n"
        "## WORKFLOW\n"
        "1. ALWAYS call get_schema_metadata first — it returns 'db_type' "
        "(the SQL dialect), 'tables' with columns and sample_rows, and "
        "'today' (use this — not your training data — for all date logic).\n"
        "   sample_rows show column FORMAT and example values only — "
        "they are NOT a complete census of the data. NEVER conclude that "
        "data is missing or a year has no records based on sample_rows. "
        "You MUST run run_readonly_sql to get real answers.\n"
        "2. Write a read-only SELECT query in the correct SQL dialect for "
        "the returned 'db_type', then call run_readonly_sql.\n"
        "   For aggregate queries (SUM, COUNT, AVG, GROUP BY) do NOT add "
        "TOP or LIMIT — the result is already bounded by the aggregation. "
        "Only add TOP / LIMIT N when fetching raw rows.\n"
        "3. If run_readonly_sql returns ok=false, fix the SQL and retry once.\n"
        "4. Never invent data — rely only on tool outputs.\n"
        "5. Present results clearly: use markdown tables for tabular data "
        "and include a brief insight after the data."
    ),
    tools=[
        FunctionTool(get_schema_metadata),
        FunctionTool(run_readonly_sql),
    ],
)

rag_agent = LlmAgent(
    name="rag_agent",
    model=_model,
    planner=_no_think,
    generate_content_config=_fast_config,
    description=(
        "Specialist for document and policy questions. Handles anything about "
        "policies, contracts, handbooks, guidelines, procedures, or any "
        "question answerable from uploaded documents."
    ),
    instruction=(
        "You are a document knowledge assistant.\n"
        "1. Call retrieve_documents with the user's question to search the "
        "knowledge base.\n"
        "2. Synthesize retrieved contexts into a clear, accurate answer.\n"
        "3. Cite which document or source the information came from.\n"
        "If no relevant documents are found or the corpus is not configured, "
        "say so explicitly."
    ),
    tools=[
        FunctionTool(retrieve_documents),
    ],
)


# ── Router (root_agent — exported for ADK) ───────────────────────────────────

root_agent = LlmAgent(
    name="agentic_rag_router",
    model=_router_model,
    planner=_no_think,
    generate_content_config=_router_config,
    description="Multi-agent router for Agentic RAG system",
    instruction=(
        "You are a smart routing agent. Analyze the user's question and "
        "delegate to the right specialist:\n\n"
        "• **database_agent** — for data, numbers, sales, orders, customers, "
        "products, counts, totals, rankings, metrics, SQL, tables, or "
        "anything answerable from a database.\n\n"
        "• **rag_agent** — for policies, documents, contracts, guidelines, "
        "handbooks, procedures, or anything answerable from uploaded "
        "documents.\n\n"
        "If the question is ambiguous, choose the most likely agent based on "
        "context. Do NOT answer questions yourself — always delegate to a "
        "specialist agent."
    ),
    sub_agents=[database_agent, rag_agent],
)
