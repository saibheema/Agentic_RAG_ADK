# Minimum Production Rollout (Tagged to Agentic_RAG)

All resources must include labels:

- `app=agentic-rag`
- `system=agentic-rag`
- `env=minprod`

## 1) Create Cloud SQL PostgreSQL

```bash
gcloud sql instances create agentic-rag-pg \
  --database-version=POSTGRES_15 \
  --cpu=2 \
  --memory=8GiB \
  --region=us-central1 \
  --storage-size=20GB \
  --storage-type=SSD \
  --availability-type=zonal \
  --labels=app=agentic-rag,system=agentic-rag,env=minprod

gcloud sql databases create agentic_rag --instance=agentic-rag-pg
gcloud sql users create app_user --instance=agentic-rag-pg --password='<STRONG_PASSWORD>'
```

## 2) Seed Schema + Sample Data

```bash
python scripts/seed_cloudsql.py \
  --instance-connection-name=<PROJECT_ID>:us-central1:agentic-rag-pg \
  --db-user=app_user \
  --db-password='<STRONG_PASSWORD>' \
  --db-name=agentic_rag \
  --sql-file=sql/min_prod_seed.sql
```

## 3) Deploy ADK Chat UI on Cloud Run

```bash
adk deploy cloud_run \
  --project=<PROJECT_ID> \
  --region=us-central1 \
  --service_name=agentic-rag-chat \
  --with_ui \
  --otel_to_cloud \
  --trace_to_cloud \
  --session_service_uri=memory:// \
  src/agentic_rag \
  -- \
  --labels=app=agentic-rag,system=agentic-rag,env=minprod \
  --add-cloudsql-instances=<PROJECT_ID>:us-central1:agentic-rag-pg \
  --set-env-vars=DB_INSTANCE_CONNECTION_NAME=<PROJECT_ID>:us-central1:agentic-rag-pg,DB_NAME=agentic_rag,DB_USER=app_user,DB_PASSWORD=<STRONG_PASSWORD>
```

## 4) Verify

- Open Cloud Run service URL and use ADK web chat.
- Ask:
  - `Show recent orders`
  - `Summarize sales by region`
  - `Who are our top customers by lifetime value?`
