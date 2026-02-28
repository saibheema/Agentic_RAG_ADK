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
```

The database agent doesn't use pre-canned queries. It reads the actual schema at runtime, writes SQL on the fly, and has a bunch of guardrails to keep things safe (read-only enforcement, keyword blocklist, auto LIMIT, query timeouts, table allowlisting).

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
scripts/               # database seeding script
sql/                   # seed SQL
tests/                 # guardrail + router tests
docs/                  # architecture, deployment, planning docs
```

## Getting started

### Prerequisites

- Python 3.12+
- A GCP project with Cloud SQL PostgreSQL set up
- `gcloud` CLI authenticated

### Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# fill in your DB credentials and GCP project in .env
```

Seed the database (if starting fresh):

```bash
python scripts/seed_cloudsql.py \
  --instance-connection-name=PROJECT:us-central1:agentic-rag-pg \
  --db-user=app_user \
  --db-password='YOUR_PASSWORD' \
  --db-name=agentic_rag \
  --sql-file=sql/min_prod_seed.sql
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

There are 37 tests covering SQL guardrails (keyword blocking, LIMIT injection, multi-statement detection, system schema blocking) and multi-agent routing behavior.

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

## Configuration

Everything is driven by environment variables. See [`.env.example`](.env.example) for the full list.

Key settings:

| Variable | What it controls |
|----------|-----------------|
| `AGENT_MODEL` | Which Gemini model to use (default: `gemini-2.5-flash`) |
| `DB_INSTANCE_CONNECTION_NAME` | Cloud SQL instance path |
| `TEXT_TO_SQL_ALLOWED_TABLES` | Comma-separated table allowlist |
| `TEXT_TO_SQL_MAX_ROWS` | Auto-injected LIMIT value (default: 200) |
| `PII_MASKING_ENABLED` | Toggle PII masking on/off |
| `VERTEX_RAG_CORPUS` | RAG corpus path (leave empty to disable document search) |
| `DB_PASSWORD_SECRET` | Secret Manager path for DB password (optional) |

## Tech stack

- **Google ADK** — agent orchestration, tool registration, session management
- **Gemini 2.5 Flash** — LLM for intent routing, SQL generation, answer synthesis
- **Cloud SQL PostgreSQL** — structured data store
- **pg8000** — pure-Python PostgreSQL driver
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
