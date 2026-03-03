# Agentic RAG — Multi-Agent Text-to-SQL & Document Retrieval

A multi-agent system that lets you query structured databases and search documents using natural language. Built with Google's [Agent Development Kit (ADK)](https://github.com/google/adk-python), powered by Gemini 2.5 Flash, and deployed on Cloud Run.

The idea is straightforward: instead of a single monolithic agent trying to do everything, there are specialized agents — one for database queries, one for document retrieval — coordinated by a supervisor that figures out which one should handle each question.

## What it does

**Ask questions in plain English, get answers from your data.**

- "How many orders were placed last month?" → generates SQL, runs it, returns the answer
- "What's our return policy?" → searches uploaded documents, synthesizes a response

The router agent examines each question and hands it off to the right specialist. No manual routing rules needed — the LLM handles intent classification.

## Architecture

```
                    ┌──────────────────┐
                    │   Router Agent   │
                    │  (supervisor)    │
                    └────┬────────┬────┘
                         │        │
              ┌──────────▼┐  ┌────▼──────────┐
              │  Database  │  │  RAG Agent    │
              │  Agent     │  │               │
              │            │  │  Vertex AI    │
              │  Text-to-  │  │  RAG Engine   │
              │  SQL + PII │  │  retrieval    │
              │  masking   │  │               │
              └──────┬─────┘  └───────────────┘
                     │
              Cloud SQL PostgreSQL
                  or SQL Server
```

The database agent doesn't use pre-canned queries. It reads the actual schema at runtime, writes SQL on the fly, and has a bunch of guardrails to keep things safe (read-only enforcement, keyword blocklist, auto LIMIT/TOP, query timeouts, table allowlisting).

It supports both PostgreSQL (Cloud SQL) and SQL Server — just set `DB_TYPE=mssql` in your environment to switch backends. The agent adapts its SQL dialect automatically based on the schema it reads.

PII masking runs on every result set before the data reaches the LLM — names, emails, SSNs get replaced with tokens like `PERSON_1`, `EMAIL_3`.

Full architecture details are in `docs/architecture.md`.

## Project layout

```
src/agentic_rag/
├── agent.py           # all agents, tools, guardrails, PII wiring
├── config.py          # pydantic settings (env var validation)
├── pii_masking.py     # regex + optional Presidio PII detection
├── tenant_config.py   # multi-tenant config scaffold (future)
└── requirements.txt   # minimal deps for Cloud Run deploy

ui/
├── index.html         # chat interface
├── app.js             # SSE client, trace panel, JSON table rendering
├── styles.css         # styling
├── nginx.conf         # reverse proxy config
└── Dockerfile         # nginx container for Cloud Run

config/                # MCP Toolbox YAML templates (reference only, not used)
scripts/               # database seeding scripts (PostgreSQL + SQL Server)
sql/                   # seed SQL (PostgreSQL + SQL Server variants)
tests/                 # guardrail + router tests
docs/                  # architecture, deployment, planning docs
```

## Getting started

### Prerequisites

- Python 3.12+
- A database: either GCP Cloud SQL PostgreSQL **or** a SQL Server instance
- `gcloud` CLI authenticated (for Cloud SQL / Cloud Run deployment)

### Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # PostgreSQL support included by default
# pip install -e ".[dev,mssql]"  # add SQL Server support
cp .env.example .env
# fill in your DB credentials and GCP project in .env
```

Seed the database (if starting fresh):

**PostgreSQL (Cloud SQL):**

```bash
python scripts/seed_cloudsql.py \
  --instance-connection-name=PROJECT:us-central1:agentic-rag-pg \
  --db-user=app_user \
  --db-password='YOUR_PASSWORD' \
  --db-name=agentic_rag \
  --sql-file=sql/min_prod_seed.sql
```

**SQL Server:**

```bash
python scripts/seed_mssql.py \
  --db-host=your-server.database.windows.net \
  --db-user=sa \
  --db-password='YOUR_PASSWORD' \
  --db-name=agentic_rag \
  --sql-file=sql/min_prod_seed_mssql.sql
```

Run locally:

```bash
adk web src/agentic_rag     # ADK dev UI at http://localhost:8000
# or
adk api_server src/agentic_rag --port=8081   # API-only mode
```

For the custom UI during local dev:

```bash
python3 -m http.server 4173 --directory ui
```

### Run tests

```bash
pytest -q
```

There are 49 tests covering SQL guardrails (keyword blocking, LIMIT/TOP injection, multi-statement detection, system schema blocking for both PG and SQL Server) and multi-agent routing behavior.

## Managing database connections

All DB connections live in **`connections.json`** at the repo root. No code changes are needed to add, remove, or switch databases.

### Switching databases in the UI

The chat UI shows a **dropdown** in the controls bar populated from `GET /databases`. Pick any connection — the agent automatically reconnects and queries the selected database. The topbar badge shows the active DB label (green dot = PostgreSQL, orange dot = SQL Server / MSSQL).

Switching mid-conversation creates a new agent session scoped to the new DB. Previous messages stay visible for reference.

### Adding a new database connection

1. **Add an entry to `connections.json`:**

   ```json
   {
     "alias": "prod_analytics",
     "label": "Prod Analytics (PostgreSQL)",
     "db_type": "postgres",
     "host": "10.0.1.50",
     "port": 5432,
     "database": "analytics",
     "user": "readonly_user",
     "password_secret": "projects/unicon-494419/secrets/prod-analytics-password/versions/latest",
     "instance_connection_name": "",
     "allowed_tables": ""
   }
   ```

   | Field | Description |
   |---|---|
   | `alias` | Unique key used internally and in session state |
   | `label` | Human-readable name shown in the UI dropdown |
   | `db_type` | `postgres` or `mssql` |
   | `allowed_tables` | Comma-separated list to restrict access, or empty to auto-discover all tables |
   | `password_secret` | Secret Manager resource path (**recommended**) |
   | `password_env` | Name of an env var holding the password (alternative to `password_secret`) |
   | `password` | Plaintext fallback — local dev only, never commit |

2. **Store the password in Secret Manager:**

   ```bash
   printf '%s' 'YOUR_PASSWORD' | gcloud secrets create prod-analytics-password \
     --data-file=- --project=unicon-494419 --replication-policy=automatic
   ```

   Or use the helper script (updates `provision_secrets.py` first):

   ```bash
   python scripts/provision_secrets.py
   ```

3. **Grant the runtime service account access:**

   ```bash
   gcloud secrets add-iam-policy-binding prod-analytics-password \
     --project=unicon-494419 \
     --member="serviceAccount:YOUR_SA@unicon-494419.iam.gserviceaccount.com" \
     --role="roles/secretmanager.secretAccessor"
   ```

4. **Restart the agent server** — `connections.json` is loaded at startup. The new DB will appear in the UI dropdown immediately.

> **No code changes required.** The agent auto-discovers tables, routes SQL correctly for the DB type, and caches schema for 24 h — all driven by the `connections.json` entry alone.

---

## Deploying to Cloud Run

### Backend

```bash
adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=us-central1 \
  --service_name=agentic-rag-chat \
  --with_ui \
  src/agentic_rag
```

Then set your env vars:

```bash
gcloud run services update agentic-rag-chat \
  --region=us-central1 \
  --set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=YOUR_PROJECT,..." 
```

### Custom UI

```bash
cd ui
gcloud run deploy agentic-rag-ui \
  --source=. \
  --region=us-central1 \
  --port=8080 \
  --allow-unauthenticated
```

The nginx config proxies `/api/*` to the backend service, so the UI and API share the same origin — no CORS headaches.

---

## Post-Deployment Verification

After every deploy, run through these checks before calling it done:

| Check | How | What you're looking for |
|-------|-----|------------------------|
| **Service health** | `gcloud run services describe agentic-rag-chat --region=us-central1 --format="value(status.url)"` | URL is returned, latest revision shows `READY` |
| **Smoke test — database** | Send: *"How many customers do we have?"* via UI or curl | Returns a number, no errors in trace panel |
| **Smoke test — routing** | Send: *"What's the return policy?"* | Routes to RAG agent (or gracefully says corpus isn't configured) |
| **PII masking** | Ask for customer details — names/emails should show as `PERSON_1`, `EMAIL_2` | Real PII never appears in the response |
| **SQL guardrails** | Try: *"DROP TABLE customers"* | Blocked with a refusal message, nothing executed |
| **UI loads** | Hit the UI service URL in a browser | Chat interface renders, settings bar visible |
| **Trace panel** | Send any query and expand the trace | Shows `transfer_to_agent` events, tool calls, timing |

Quick curl smoke test:

```bash
curl -sS -X POST "https://YOUR-BACKEND-URL/run" \
  -H "Content-Type: application/json" \
  -d '{
    "appName":"agentic_rag",
    "userId":"smoke-test",
    "sessionId":"smoke-001",
    "newMessage":{"role":"user","parts":[{"text":"how many products exist?"}]},
    "streaming":false
  }' | python3 -m json.tool
```

## Observability & Monitoring

Everything runs on Cloud Run, so GCP's built-in observability stack does the heavy lifting. No extra instrumentation needed — just know where to look.

### Logs

All agent activity, SQL execution, and errors flow into **Cloud Logging** automatically.

```bash
# tail live logs from the backend service
gcloud run services logs tail agentic-rag-chat --region=us-central1

# search for SQL execution errors in the last hour
gcloud logging read \
  'resource.type="cloud_run_revision" AND resource.labels.service_name="agentic-rag-chat" AND severity>=ERROR' \
  --limit=20 --format="table(timestamp, textPayload)"

# find PII masking activity
gcloud logging read \
  'resource.type="cloud_run_revision" AND textPayload:"PII"' \
  --limit=10
```

**Where in the GCP Console:**  
Cloud Run → `agentic-rag-chat` → **Logs** tab. Filter by severity to spot errors quickly.

### Metrics & Dashboards

Cloud Run auto-tracks these out of the box:

| Metric | Where to find it | What to watch |
|--------|-------------------|---------------|
| **Request count** | Cloud Run → Metrics tab | Traffic patterns, spikes |
| **Request latency (p50/p95/p99)** | Cloud Run → Metrics tab | p95 should stay under 10s for SQL queries |
| **Container instance count** | Cloud Run → Metrics tab | Scaling behavior, cold starts |
| **Memory utilization** | Cloud Run → Metrics tab | Stay under 80% of your limit |
| **CPU utilization** | Cloud Run → Metrics tab | Correlates with LLM call volume |
| **Error rate (5xx)** | Cloud Run → Metrics tab | Should be near zero |
| **Cloud SQL connections** | Cloud SQL → Overview → Connections | Watch for connection exhaustion |
| **Cloud SQL query latency** | Cloud SQL → Query insights | Slow queries, missing indexes |

**Set up alerts** (recommended):

```bash
# alert if error rate exceeds 5% over 5 minutes
gcloud monitoring policies create \
  --display-name="agentic-rag-high-errors" \
  --condition-display-name="5xx error rate > 5%" \
  --condition-filter='resource.type="cloud_run_revision" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"'
```

### Tracing

**Cloud Trace** captures end-to-end request timelines automatically on Cloud Run. To inspect:

1. GCP Console → **Trace** → filter by service `agentic-rag-chat`
2. Look at the waterfall to see where time is spent — LLM calls vs. SQL execution vs. network
3. The custom UI's trace panel also shows agent-level flow (which agent handled the query, what tools were called)

### Error Reporting

Cloud Run errors automatically surface in **Error Reporting** (GCP Console → Error Reporting). Group by error type to catch recurring issues like:

- Cloud SQL connection timeouts
- Secret Manager permission denials
- Malformed SQL queries that slip past guardrails

## Performance

### Latency breakdown

A typical query goes through these stages:

```
User question
  → Router Agent LLM call (~1-2s)
    → Database Agent LLM call for SQL generation (~1-3s)
      → SQL execution on Cloud SQL (~50-200ms)
        → PII masking (~5-20ms)
          → Answer synthesis LLM call (~1-2s)
            → Response
Total: 3-8s typical | 10-15s for complex joins
```

Most of the time is spent in LLM calls, not in SQL execution or PII masking.

### Tuning levers

| Lever | Setting | Impact |
|-------|---------|--------|
| **Query timeout** | `TEXT_TO_SQL_QUERY_TIMEOUT_MS` (default: 30000) | Kills runaway queries |
| **Row limit** | `TEXT_TO_SQL_MAX_ROWS` (default: 200) | Caps response size, reduces LLM token cost |
| **Table allowlist** | `TEXT_TO_SQL_ALLOWED_TABLES` | Leave empty to auto-discover all tables from the DB. Set to a comma-separated list to restrict access to specific tables only |
| **Schema cache TTL** | `SCHEMA_CACHE_TTL_SECONDS` (default: 86400) | Caches table/column schema in-memory; eliminates a DB round-trip on every query. Set to `0` to disable. Auto-invalidates on config change or process restart |
| **Model choice** | `AGENT_MODEL` | Flash is fast + cheap; Pro is slower but better at complex JOINs |
| **Cold starts** | Set `--min-instances=1` on Cloud Run | Eliminates first-request penalty (~5-8s) |
| **Concurrency** | `--concurrency` on Cloud Run (default: 80) | Lower if memory-bound |

### Scaling

Cloud Run auto-scales based on request volume. Things to keep in mind:

- Each container instance opens its own Cloud SQL connection — set `--max-instances` to avoid overwhelming the database
- Gemini API has per-project quotas — monitor `aiplatform.googleapis.com/quota` metrics if you see 429s
- The UI service (`agentic-rag-ui`) is just nginx serving static files, it scales with zero concern

## Security

### What's already in place

| Layer | Mechanism |
|-------|-----------|
| **SQL injection prevention** | Keyword blocklist (`DROP`, `DELETE`, `ALTER`, etc.), read-only enforcement, multi-statement blocking |
| **Data exposure** | PII masking on all query results before they reach the LLM |
| **Row-level limiting** | Auto-injected `LIMIT` (PostgreSQL) or `TOP` (SQL Server) prevents bulk data exfiltration |
| **Table access control** | `TEXT_TO_SQL_ALLOWED_TABLES` restricts which tables the agent can see |
| **System schema blocking** | Blocks queries against `pg_catalog`, `information_schema`, `sys` (except via the schema tool) |
| **Credential management** | DB password via Secret Manager (`DB_PASSWORD_SECRET`) — no plaintext in env vars in production |
| **Network** | Cloud SQL uses private IP; no public database endpoint exposed |

### Hardening for production

Things you should add before going live with real users:

- [ ] **IAM authentication** on Cloud Run — remove `--allow-unauthenticated`, add Identity-Aware Proxy or API Gateway
- [ ] **VPC connector** — route Cloud Run → Cloud SQL over private network only
- [ ] **Audit logging** — enable Cloud SQL audit logs and Data Access logs
- [ ] **Rate limiting** — add Cloud Armor or API Gateway rate limits
- [ ] **Input validation** — cap message length at the API layer
- [ ] **Session cleanup** — purge old ADK sessions to prevent context accumulation

## Testing Strategy

### Current coverage

| Suite | File | Tests | What it covers |
|-------|------|-------|----------------|
| **SQL guardrails** | `tests/test_sql_guardrails.py` | 42 | Keyword blocking, LIMIT/TOP injection, multi-statement detection, system schema access (`pg_catalog` + `sys`), `SELECT`-only enforcement, table allowlisting |
| **Router behavior** | `tests/test_router_agent.py` | 7 | Intent classification — database vs. document vs. ambiguous queries route to the right agent |
| **Accuracy (live)** | `tests/test_accuracy.py` | 12 | End-to-end SQL generation against the deployed backend — verifies real queries return correct data |

Run unit tests (no network needed):

```bash
pytest tests/test_sql_guardrails.py tests/test_router_agent.py -q
# 37 passed
```

Run accuracy tests (needs a running backend):

```bash
pytest tests/test_accuracy.py -q --tb=short
# 11/12 passed (92%) — the 1 failure is a session context issue, not SQL generation
```

### What's not covered yet

- **Load testing** — use [Locust](https://locust.io) or [k6](https://k6.io) to simulate concurrent users
- **Integration tests** — test Cloud SQL connectivity, Secret Manager access, RAG corpus retrieval in a staging environment
- **Contract tests** — validate the ADK `/run` endpoint request/response schema
- **UI tests** — Playwright or Cypress for chat flow, trace panel rendering, SSE streaming

## CI/CD

There's no automated pipeline yet — deployment is manual via `adk deploy cloud_run` and `gcloud run deploy`. If you want to automate:

**Suggested GitHub Actions workflow:**

```
on push to main:
  1. pytest (unit + guardrail tests)
  2. docker build (backend + UI)
  3. push to Artifact Registry
  4. gcloud run deploy (staging)
  5. run accuracy tests against staging
  6. manual approval gate
  7. gcloud run deploy (production)
```

Key things to wire up:

- **Workload Identity Federation** for keyless GCP auth from GitHub Actions
- **Artifact Registry** for container images instead of building on deploy
- **Separate staging service** (`agentic-rag-chat-staging`) with its own Cloud SQL database
- **Branch protection** — require passing tests before merge

---

## Configuration

Everything is driven by environment variables. See [`.env.example`](.env.example) for the full list.

Key settings:

| Variable | What it controls |
|----------|-----------------|
| `DB_TYPE` | Database backend: `postgres` (default) or `mssql` for SQL Server |
| `AGENT_MODEL` | Which Gemini model to use (default: `gemini-3.0-flash-preview`) |
| `DB_INSTANCE_CONNECTION_NAME` | Cloud SQL instance path (PostgreSQL only) |
| `DB_HOST` | Database hostname (required for SQL Server, optional for Cloud SQL) |
| `DB_PORT` | Database port (auto-defaults: 5432 for PG, 1433 for MSSQL) |
| `TEXT_TO_SQL_ALLOWED_TABLES` | Comma-separated table allowlist. **Leave empty (default) to auto-discover all user tables from the connected database** — no manual listing needed. Set to restrict access to specific tables only (e.g. `Sales,Customers,Products`) |
| `TEXT_TO_SQL_MAX_ROWS` | Auto-injected LIMIT/TOP value (default: 200) |
| `SCHEMA_CACHE_TTL_SECONDS` | How long (seconds) to cache table/column schema in-memory (default: `86400` = 24 h). Set to `0` to always fetch fresh from the DB. Cache key includes host, database, DB type, and allowed tables — any config change auto-invalidates it |
| `PII_MASKING_ENABLED` | Toggle PII masking on/off |
| `VERTEX_RAG_CORPUS` | RAG corpus path (leave empty to disable document search) |
| `DB_PASSWORD_SECRET` | Secret Manager path for DB password (optional) |

## Tech stack

- **Google ADK** — agent orchestration, tool registration, session management
- **Gemini 2.5 Flash** — LLM for intent routing, SQL generation, answer synthesis
- **Cloud SQL PostgreSQL** — structured data store (default)
- **SQL Server** — alternative structured data store (via `pymssql`)
- **pg8000** — pure-Python PostgreSQL driver
- **pymssql** — pure-Python SQL Server driver (optional)
- **Vertex AI RAG Engine** — managed document chunking, embedding, and retrieval
- **Pydantic Settings** — env var parsing and validation
- **Nginx** — reverse proxy for the custom UI
- **Cloud Run** — serverless hosting for both backend and UI

## Documentation

Architecture details, deployment checklists, and planning docs are maintained locally in the `docs/` folder:

- `architecture.md` — full system architecture, tool definitions, data flow, accuracy benchmarks
- `prod-readiness.md` — production deployment checklist and infrastructure requirements
- `min-prod-rollout.md` — step-by-step cloud resource setup commands
- `implementation-plan.md` — phased build plan with testing strategy
- `design-reference.md` — original architecture vision and reference repos
