# Production Readiness Blueprint

This document maps production requirements to the current implementation.

## Architecture

```
Router Agent (agentic_rag_router) — ADK LlmAgent supervisor
  ├─ database_agent  — Text-to-SQL via pg8000 + guardrails
  └─ rag_agent       — Vertex AI RAG Engine retrieval
```

All agent definitions live in a single file: `src/agentic_rag/agent.py`.

## 1) Multi-Agent Routing

- Implemented: ADK `LlmAgent` with `sub_agents=[database_agent, rag_agent]`.
- The LLM supervisor decides which sub-agent to delegate to based on the question.
- No heuristic routing code on the hot path — the model handles intent classification.
- File: `src/agentic_rag/agent.py`

## 2) Text-to-SQL (Database Agent)

- LLM generates SQL dynamically from schema metadata (not pre-canned queries).
- Guardrails: read-only enforcement, blocked DML keywords, allowed-table list,
  auto LIMIT injection, statement_timeout.
- Database connection: pg8000 with multi-fallback (TCP → Unix socket → socket+suffix).
- File: `src/agentic_rag/agent.py` — tools `get_schema_metadata`, `run_readonly_sql`

## 3) Vertex AI RAG Engine

- Implemented: `retrieve_documents` tool calls `vertexai.preview.rag.retrieval_query`.
- Controlled by `VERTEX_RAG_CORPUS` env var; gracefully returns "not configured" when empty.
- Recommended next steps:
  - Cloud Run Job for batch ingestion (GCS → chunk/embed/import into RAG corpus)
  - Event trigger from GCS to invoke ingestion job
- File: `src/agentic_rag/agent.py` — tool `retrieve_documents`

## 4) PII Masking

- Implemented: `PIIMasker` with regex fallback (email, SSN, name) + optional Presidio.
- Wired into `run_readonly_sql` — all query results are masked before reaching the LLM.
- Controlled by `PII_MASKING_ENABLED`, `PII_USE_PRESIDIO`, `PII_DEFAULT_RULES`.
- Files: `src/agentic_rag/agent.py`, `src/agentic_rag/pii_masking.py`

## 5) DB Credential Security

- Supports plain `DB_PASSWORD` env var (dev) or Secret Manager via `DB_PASSWORD_SECRET`.
- When `DB_PASSWORD_SECRET` is set (e.g., `projects/P/secrets/S/versions/latest`),
  the password is loaded from Secret Manager at startup.
- File: `src/agentic_rag/agent.py` — `_resolve_db_password()`

## 6) Multi-Tenant Config (future)

- Scaffolded: tenant resolver with Firestore + Secret Manager integration.
- Not wired into agent.py yet — for future per-tenant DB/corpus/PII config.
- File: `src/agentic_rag/tenant_config.py`

## 7) Auth + Security

- Required infra controls (not code-only):
  - IAP or Identity Platform in front of Cloud Run
  - JWT claim extraction for tenant mapping
  - VPC Service Controls perimeter
  - Cloud Armor WAF + DDoS protections

## 8) Observability + Evaluation

- Recommended:
  - `--otel_to_cloud` and `--trace_to_cloud` flags in ADK deploy
  - RAG eval pipeline in Vertex AI for faithfulness/relevancy/precision
  - Token usage streaming to BigQuery per `tenant_id`
  - Cloud Monitoring SLO alerts for latency/error/quota

## Deployment

```bash
adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=us-central1 \
  --service_name=agentic-rag-chat \
  --with_ui \
  --otel_to_cloud \
  --trace_to_cloud \
  --session_service_uri=memory:// \
  src/agentic_rag
```

The deployed service exposes ADK API endpoints including `/run` and `/run_sse`.

## Reference Config Templates

The `config/tools.*.yaml` files are reference templates for a potential MCP Toolbox
migration (pre-defined SQL queries). They are **not used** by the current Text-to-SQL
approach. See `config/README.md` for details.
