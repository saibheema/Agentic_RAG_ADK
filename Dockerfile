# ── Agentic RAG — Cloud Run image ───────────────────────────────────────────
# Build: docker build -t agentic-rag .
# Deployed via:  gcloud run deploy agentic-rag --source .
# Python 3.12, ADK 1.5.0, connects to Cloud SQL (PG) + Youngsinc SQL Server
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim

# System deps for pymssql (FreeTDS) and pg8000 (pure Python, no native deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
        freetds-dev \
        libssl-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python dependencies ──────────────────────────────────────────────
# Copy only dependency files first for layer caching
COPY pyproject.toml ./
COPY src/agentic_rag/requirements.txt ./requirements.txt

# Install the package in editable mode with mssql + secrets + tenants extras
# uvicorn / fastapi come in via google-adk
RUN pip install --no-cache-dir \
        "uvicorn[standard]>=0.29" \
        "fastapi>=0.111" \
        "python-multipart>=0.0.9" \
        "google-cloud-secret-manager>=2.20.0" \
        "google-cloud-firestore>=2.18.0" \
        "firebase-admin>=6.5.0" \
        "requests>=2.32.0" \
        pymssql \
        pg8000 \
        "google-adk==1.5.0" \
        "google-genai>=1.23.0" \
        "google-cloud-aiplatform>=1.74.0" \
        "pydantic>=2.8.0" \
        "pydantic-settings>=2.4.0"

# ── Copy application source ──────────────────────────────────────────────────
COPY src/ ./src/
COPY ui/ ./ui/
COPY connections.json ./connections.json
COPY run_local.py ./run_local.py

# ── Install package itself (editable not needed in prod) ─────────────────────
RUN pip install --no-cache-dir -e . --no-deps

# ── Runtime config ───────────────────────────────────────────────────────────
ENV PYTHONPATH=/app/src
ENV PORT=8080

EXPOSE 8080

# Health-check so Cloud Run knows when the container is ready
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8080/ || exit 1

CMD ["python", "run_local.py"]
