# Architecture & Tooling Reference

## System Overview

This project implements a multi-agent Retrieval-Augmented Generation (RAG) system built on Google's Agent Development Kit (ADK). It combines two retrieval strategies under a single routing layer:

- **Text-to-SQL** for structured data queries against PostgreSQL
- **Vertex AI RAG Engine** for unstructured document search

The system runs on Google Cloud Run with a custom web UI.

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│  Browser                                                         │
│  Custom UI (index.html + app.js + styles.css)                   │
│  Hosted on Cloud Run (agentic-rag-ui)                           │
└──────────────┬───────────────────────────────────────────────────┘
               │  /api/*  (same-origin)
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Nginx Reverse Proxy  (Cloud Run — port 8080)                   │
│  • Static files at /                                             │
│  • Proxies /api/* → ADK backend (no CORS needed)                │
│  • SSE streaming support                                        │
└──────────────┬───────────────────────────────────────────────────┘
               │  HTTPS
               ▼
┌──────────────────────────────────────────────────────────────────┐
│  ADK Backend  (Cloud Run — agentic-rag-chat)                    │
│  google-adk → FastAPI with SSE streaming                        │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │         root_agent (agentic_rag_router)                    │  │
│  │         LlmAgent — Gemini 2.5 Flash                        │  │
│  │         Role: Supervisor / Intent Router                   │  │
│  │                                                            │  │
│  │   Delegates to sub-agents via transfer_to_agent            │  │
│  │         ┌──────────────┬──────────────────┐                │  │
│  │         ▼              ▼                  │                │  │
│  │  ┌─────────────┐ ┌──────────────┐         │                │  │
│  │  │ database_   │ │  rag_agent   │         │                │  │
│  │  │   agent     │ │              │         │                │  │
│  │  │             │ │  Tool:       │         │                │  │
│  │  │ Tools:      │ │  • retrieve_ │         │                │  │
│  │  │ • get_      │ │    documents │         │                │  │
│  │  │   schema_   │ │              │         │                │  │
│  │  │   metadata  │ └──────┬───────┘         │                │  │
│  │  │ • run_      │        │                 │                │  │
│  │  │   readonly_ │        ▼                 │                │  │
│  │  │   sql       │  Vertex AI RAG Engine    │                │  │
│  │  └──────┬──────┘  (managed corpus)        │                │  │
│  │         │                                 │                │  │
│  └─────────┼─────────────────────────────────┘                │  │
│            │                                                    │
│    ┌───────▼────────┐    ┌──────────────┐                      │
│    │ SQL Guardrails  │    │ PII Masker   │                      │
│    │ • SELECT only   │    │ • regex      │                      │
│    │ • blocked DML   │    │ • Presidio   │                      │
│    │ • LIMIT inject  │    │   (optional) │                      │
│    │ • timeout       │    │              │                      │
│    │ • table allow   │    └──────┬───────┘                     │
│    └───────┬─────────┘           │                             │
│            │                     │ masks query results          │
│            ▼                     │                              │
│    ┌───────────────┐◄────────────┘                             │
│    │  pg8000        │                                           │
│    │  (PostgreSQL)  │                                           │
│    └───────┬────────┘                                           │
└────────────┼────────────────────────────────────────────────────┘
             │  Cloud SQL Auth Proxy
             ▼
┌──────────────────────────────────────────────────────────────────┐
│  Cloud SQL PostgreSQL (private IP)                               │
│  Tables: customers, orders, order_items, products               │
└──────────────────────────────────────────────────────────────────┘
```

## Agent Design

### Router Agent (`agentic_rag_router`)

The entry point. Uses Gemini to classify user intent and delegates to the appropriate specialist. It never answers questions directly — it always routes.

**Routing rules (LLM-driven, not heuristic):**
- Data / numbers / SQL / analytics → `database_agent`
- Policies / documents / contracts / guidelines → `rag_agent`

### Database Agent (`database_agent`)

Handles structured data questions using dynamic Text-to-SQL:

1. Calls `get_schema_metadata()` to discover tables and columns
2. Writes a read-only SQL query based on the schema
3. Calls `run_readonly_sql(sql)` to execute with guardrails
4. If execution fails, reads the error, fixes the SQL, and retries

### RAG Agent (`rag_agent`)

Handles unstructured document questions:

1. Calls `retrieve_documents(query)` to search the Vertex AI RAG corpus
2. Synthesizes retrieved chunks into a coherent answer
3. Cites source documents

## Tools (Function Definitions)

### `get_schema_metadata()`

Queries `information_schema.columns` for allowed tables and returns table names, column names, and data types. Gives the LLM accurate schema context before it writes SQL.

### `run_readonly_sql(sql: str)`

Executes LLM-generated SQL with these safety layers:

| Guardrail | Description |
|-----------|-------------|
| Read-only check | Only `SELECT` / `WITH` statements allowed |
| Keyword blocklist | Rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, `TRUNCATE`, `GRANT`, `REVOKE`, `MERGE`, `CALL`, `EXECUTE`, `COPY` |
| Multi-statement block | Rejects queries with `;` in the middle |
| System schema block | Blocks `information_schema.*` and `pg_catalog.*` |
| Auto LIMIT | Appends `LIMIT {max_rows}` when no limit clause is present |
| Statement timeout | Sets `statement_timeout` on the connection (default 15s) |
| Table allowlist | Only tables in `TEXT_TO_SQL_ALLOWED_TABLES` are visible via schema metadata |

### `retrieve_documents(query: str)`

Calls `vertexai.preview.rag.retrieval_query()` to semantic-search a managed RAG corpus. Returns top-5 document chunks with source URIs and relevance scores. Controlled by `VERTEX_RAG_CORPUS` env var.

## PII Masking

Applied to every SQL result row before it reaches the LLM. Two modes:

- **Regex (default)** — catches names (`FirstName LastName` → `PERSON_1`), emails → `EMAIL_1`, SSNs → `SSN_1`. Uses consistent tokenization so the same entity always maps to the same placeholder.
- **Presidio (optional)** — Microsoft's NER-based PII engine. Enable with `PII_USE_PRESIDIO=true`.

Controlled by `PII_MASKING_ENABLED`, `PII_USE_PRESIDIO`, `PII_DEFAULT_RULES`.

## Credential Management

`_resolve_db_password()` follows a two-step resolution:

1. If `DB_PASSWORD_SECRET` is set (format: `projects/PROJECT/secrets/NAME/versions/latest`), loads the password from Google Secret Manager
2. Falls back to `DB_PASSWORD` env var with a warning log

## Database Connectivity

pg8000 (pure-Python PostgreSQL driver) with multi-fallback connection:

1. TCP to `127.0.0.1:5432` (Cloud SQL Auth Proxy sidecar)
2. Unix socket at `/cloudsql/{instance_connection_name}`
3. Unix socket with `.s.PGSQL.5432` suffix

## Technology Stack

| Component | Technology | Version |
|-----------|-----------|---------|
| Agent framework | Google ADK | 1.5.0 |
| LLM | Gemini 2.5 Flash | latest |
| Backend runtime | FastAPI (via ADK) | — |
| PostgreSQL driver | pg8000 | ≥ 1.31.2 |
| Document retrieval | Vertex AI RAG Engine | via google-cloud-aiplatform ≥ 1.74.0 |
| PII detection | Regex + Presidio (optional) | — |
| Settings | Pydantic Settings | ≥ 2.4.0 |
| Secret management | Google Secret Manager | optional |
| Hosting | Google Cloud Run | 2 services |
| UI proxy | Nginx | 1.27-alpine |
| Database | Cloud SQL PostgreSQL 15 | — |

## Deployment Topology

Two Cloud Run services:

| Service | Role | Image |
|---------|------|-------|
| `agentic-rag-chat` | ADK backend (FastAPI + SSE) | Auto-built by `adk deploy cloud_run` |
| `agentic-rag-ui` | Custom UI + nginx reverse proxy | `nginx:1.27-alpine` |

The nginx proxy serves the static UI at `/` and forwards `/api/*` to the backend, eliminating CORS requirements.

## Request Flow

```
User: "What is the total revenue from all orders?"

1. Browser → POST /api/run_sse → nginx → ADK backend
2. root_agent (Gemini) → classifies as data question → transfer_to_agent(database_agent)
3. database_agent (Gemini) → calls get_schema_metadata()
   → receives 4 tables, 19 columns
4. database_agent (Gemini) → generates SQL → calls run_readonly_sql("SELECT SUM(total_amount) FROM orders")
   → guardrail validation passes
   → LIMIT 200 appended
   → pg8000 executes → [{total: 13082.0}]
   → PII masking applied (no PII in numeric data, passes through)
5. database_agent (Gemini) → "The total revenue is $13,082."
6. SSE streams all events → UI renders answer + trace panel
```

## Environment Variables

See `.env.example` for the full list. Key groups:

- **Cloud / Auth**: `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_GENAI_USE_VERTEXAI`
- **Model**: `AGENT_MODEL`
- **Database**: `DB_INSTANCE_CONNECTION_NAME`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PASSWORD_SECRET`
- **Guardrails**: `TEXT_TO_SQL_ALLOWED_TABLES`, `TEXT_TO_SQL_MAX_ROWS`, `TEXT_TO_SQL_QUERY_TIMEOUT_MS`
- **PII**: `PII_MASKING_ENABLED`, `PII_USE_PRESIDIO`, `PII_DEFAULT_RULES`
- **RAG**: `VERTEX_RAG_CORPUS`
- **Tenant** (future): `TENANT_CONFIG_USE_FIRESTORE`, `TENANT_DEFAULT_DB_TYPE`

## Accuracy

Tested with 12 query types (counts, aggregations, filters, JOINs, GROUP BY, subqueries, date range, DISTINCT). Results:

- **SQL execution success**: 12/12 (100%)
- **Answer accuracy**: 11/12 (92%)
- The single miss was a session-context issue in a long multi-turn conversation, not a SQL generation failure

Accuracy stays high because:
1. Small, clean schema (4 tables, 19 columns) fits easily in LLM context
2. Schema-first workflow ensures the LLM sees real column names before writing SQL
3. Retry-on-error instruction lets the agent self-correct
4. Guardrails catch dangerous operations before execution
