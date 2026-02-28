"""
Multi-agent Agentic RAG — ADK entry point.

Architecture (Agentic_RAG.md):
  Router Agent (supervisor)
    ├─ Database Agent  — Text-to-SQL against PostgreSQL
    └─ RAG Agent       — Document retrieval via Vertex AI RAG Engine

PII masking is applied to all database query results before they reach the LLM.
"""

from __future__ import annotations

import os
import re
from decimal import Decimal
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
import pg8000


# ── PII masking ──────────────────────────────────────────────────────────────

_PII_ENABLED = os.environ.get("PII_MASKING_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
_PII_RULES = [
    r.strip()
    for r in os.environ.get("PII_DEFAULT_RULES", "name,ssn,email").split(",")
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
    """Apply PII masking to string values in result rows."""
    m = _masker()
    if not m or not _PII_RULES:
        return rows
    for row in rows:
        for key, value in row.items():
            if isinstance(value, str):
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


def _db_config() -> dict[str, Any]:
    return {
        "user": os.environ.get("DB_USER", "app_user"),
        "password": _resolve_db_password(),
        "database": os.environ.get("DB_NAME", "agentic_rag"),
        "host": os.environ.get("DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "instance_connection_name": os.environ.get(
            "DB_INSTANCE_CONNECTION_NAME", ""
        ),
        "max_rows": int(os.environ.get("TEXT_TO_SQL_MAX_ROWS", "200")),
        "query_timeout_ms": int(
            os.environ.get("TEXT_TO_SQL_QUERY_TIMEOUT_MS", "15000")
        ),
        "allowed_tables": [
            table.strip()
            for table in os.environ.get(
                "TEXT_TO_SQL_ALLOWED_TABLES",
                "orders,customers,products,order_items",
            ).split(",")
            if table.strip()
        ],
    }


def _connect():
    cfg = _db_config()
    instance = cfg["instance_connection_name"]
    if instance:
        socket_dir = f"/cloudsql/{instance}"
        try:
            return pg8000.connect(
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                host="127.0.0.1",
                port=5432,
            )
        except Exception:
            try:
                return pg8000.connect(
                    user=cfg["user"],
                    password=cfg["password"],
                    database=cfg["database"],
                    unix_sock=socket_dir,
                )
            except Exception:
                return pg8000.connect(
                    user=cfg["user"],
                    password=cfg["password"],
                    database=cfg["database"],
                    unix_sock=f"{socket_dir}/.s.PGSQL.5432",
                )

    return pg8000.connect(
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        host=cfg["host"],
        port=cfg["port"],
    )


def _to_rows(cursor) -> list[dict[str, Any]]:
    cols = [item[0] for item in cursor.description] if cursor.description else []
    out: list[dict[str, Any]] = []
    for row in cursor.fetchall():
        converted: list[Any] = []
        for value in row:
            if isinstance(value, Decimal):
                converted.append(float(value))
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

    if " information_schema." in padded or " pg_catalog." in padded:
        return False, "System schemas are blocked"

    return True, "ok"


def _inject_limit_if_missing(sql: str, max_rows: int) -> str:
    normalized = _normalized_sql(sql).lower()
    if " limit " in f" {normalized} ":
        return sql
    sql = sql.rstrip().rstrip(";")
    return f"{sql} LIMIT {max_rows}"


# ── Database Agent tools ─────────────────────────────────────────────────────


def get_schema_metadata() -> dict[str, Any]:
    """Return table/column schema metadata for allowed business tables."""
    cfg = _db_config()
    allowed_tables = cfg["allowed_tables"]

    if not allowed_tables:
        return {"tables": []}

    placeholders = ", ".join(["%s"] * len(allowed_tables))
    sql = f"""
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ({placeholders})
    ORDER BY table_name, ordinal_position
    """

    conn = _connect()
    try:
        cur = conn.cursor()
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

        return {
            "tables": [
                {"table": table_name, "columns": columns}
                for table_name, columns in tables.items()
            ]
        }
    finally:
        conn.close()


def run_readonly_sql(sql: str) -> dict[str, Any]:
    """Execute LLM-generated read-only SQL against allowed tables with guardrails."""
    cfg = _db_config()
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

    final_sql = _inject_limit_if_missing(sql, max_rows)

    conn = _connect()
    try:
        cur = conn.cursor()
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


# ── Agent definitions ────────────────────────────────────────────────────────

_model = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")

database_agent = LlmAgent(
    name="database_agent",
    model=_model,
    description=(
        "Specialist for structured data questions. Handles anything about "
        "orders, customers, products, sales, counts, totals, rankings, "
        "averages, or any question answerable with SQL."
    ),
    instruction=(
        "You are a database analytics assistant with Text-to-SQL capability.\n"
        "1. Call get_schema_metadata to discover available tables and columns.\n"
        "2. Write a read-only SELECT or WITH query and call run_readonly_sql.\n"
        "3. If run_readonly_sql returns ok=false, read the error, fix the SQL, "
        "and retry.\n"
        "Never invent data — rely only on tool outputs.\n"
        "Keep final answers concise and include key numbers."
    ),
    tools=[
        FunctionTool(get_schema_metadata),
        FunctionTool(run_readonly_sql),
    ],
)

rag_agent = LlmAgent(
    name="rag_agent",
    model=_model,
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
    model=_model,
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
