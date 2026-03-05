# Cloud Environment Setup

Complete reference for the GCP infrastructure, VPN relay, and Cloud Run deployment backing this project.

---

## GCP Project

| Item | Value |
|---|---|
| Project ID | `unicon-494419` |
| Region | `us-central1` |
| Auth account | `sbheema@swardesi.com` |

---

## Architecture Overview

```
Browser / UI
    │
    ▼
Cloud Run: agentic-rag-chat
    │  (Vertex AI Gemini for LLM)
    │  (VPC Connector → private egress)
    │
    ▼
VPC Connector: ai-factory-connector (10.8.0.0/28)
    │
    ▼
GCE VM: yisbeta-vpn-relay (10.128.0.3)
    │  Docker container: youngsinc-tunnel
    │  socat: 0.0.0.0:1433 → 10.0.0.22:1433
    │
    ▼
L2TP/IPSec VPN Tunnel (ppp0 → 10.20.30.210)
    │
    ▼
YISBeta SQL Server (10.0.0.22:1433)
```

---

## Cloud Run Service

| Item | Value |
|---|---|
| Service name | `agentic-rag-chat` |
| Region | `us-central1` |
| URL | `https://agentic-rag-chat-6slvib5z6a-uc.a.run.app` |
| UI | `https://agentic-rag-chat-6slvib5z6a-uc.a.run.app/app/` |
| API health | `https://agentic-rag-chat-6slvib5z6a-uc.a.run.app/healthz/db` |
| Container image | `gcr.io/unicon-494419/agentic-rag-adk:latest` |
| VPC connector | `ai-factory-connector` |
| VPC egress | `private-ranges-only` |

### Environment Variables

| Variable | Value |
|---|---|
| `AGENT_MODEL` | `gemini-2.5-flash-lite` |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` |
| `GOOGLE_CLOUD_PROJECT` | `unicon-494419` |
| `GOOGLE_CLOUD_LOCATION` | `us-central1` |
| `DB_DEFAULT_ALIAS` | `yisbeta_relay_vm` |

### Build & Deploy

```bash
# Build
gcloud builds submit \
  --tag gcr.io/unicon-494419/agentic-rag-adk:latest \
  --project=unicon-494419

# Deploy
gcloud run deploy agentic-rag-chat \
  --image gcr.io/unicon-494419/agentic-rag-adk:latest \
  --project=unicon-494419 \
  --region=us-central1

# Update env vars (use --update-env-vars to avoid wiping existing vars)
gcloud run services update agentic-rag-chat \
  --region=us-central1 \
  --update-env-vars="KEY=VALUE"
```

---

## VPC Connector

| Item | Value |
|---|---|
| Name | `ai-factory-connector` |
| Network | `default` |
| IP range | `10.8.0.0/28` |
| Region | `us-central1` |

Cloud Run uses this connector for all egress to private IP ranges (SQL Server relay VM, etc.).

---

## VPN Relay VM

| Item | Value |
|---|---|
| Name | `yisbeta-vpn-relay` |
| Zone | `us-central1-f` |
| Machine type | `e2-micro` |
| Internal IP | `10.128.0.3` |
| External IP | None (IAP tunnel only) |
| Network tags | `vpn-relay`, `yisbeta-vpn-relay` |

### SSH Access (via IAP — no external IP needed)

```bash
gcloud compute ssh yisbeta-vpn-relay \
  --project=unicon-494419 \
  --zone=us-central1-f \
  --tunnel-through-iap \
  --quiet
```

### Firewall Rule

```
Rule: allow-cloudrun-to-vpn-relay
Direction: INGRESS
Target tags: yisbeta-vpn-relay
Source ranges: 10.8.0.0/28  (VPC connector range)
Allowed: tcp:1433
```

### Routing Fix (persistent)

The VPN container adds a `10.0.0.0/8 via ppp0` host route that intercepts reply packets destined for Cloud Run's `10.8.0.2`. A static route is injected at boot to fix this:

```bash
ip route add 10.8.0.0/28 via 10.128.0.1 dev ens4
```

Persisted via two mechanisms:
- `/etc/rc.local` — runs on every boot
- `/etc/systemd/system/vpn-routing-fix.service` — runs after `docker.service`

---

## Docker VPN Container

| Item | Value |
|---|---|
| Container name | `youngsinc-tunnel` |
| Image | `youngsinc-vpn-tunnel` (built locally on VM) |
| Restart policy | `unless-stopped` |
| Network mode | `--network=host` |
| VPN type | L2TP/IPSec (IKEv1), PSK auth |
| VPN server | `remote.youngsinc.com` (72.240.11.135) |
| Tunnel interface | `ppp0` → assigned IP `10.20.30.210` |
| socat forward | `0.0.0.0:1433` → `10.0.0.22:1433` via ppp0 |

### Check VPN Status

```bash
# Check ppp0 is up with an IP
sudo docker exec youngsinc-tunnel ip addr show ppp0 | grep inet

# Check socat is listening
sudo docker exec youngsinc-tunnel ss -tlnp | grep 1433

# Tail container logs
sudo docker logs youngsinc-tunnel --tail=50 -f

# Check container restart count (should stay 0)
sudo docker inspect youngsinc-tunnel --format='{{.RestartCount}}'
```

### Rebuild VPN Container

```bash
sudo docker rm -f youngsinc-tunnel
cd /opt/Agentic_RAG_ADK && sudo git pull
cd scripts/docker_vpn && sudo docker build -t youngsinc-vpn-tunnel .
# Then restart via setup script or manually with credentials
```

---

## SQL Server (YISBeta)

| Item | Value |
|---|---|
| Host (VPN-side) | `10.0.0.22` |
| Port | `1433` |
| Database | `YISBeta` |
| User | `3vAnalysts2` |
| Reachable via | `yisbeta-vpn-relay:1433` → socat → VPN → `10.0.0.22:1433` |
| Password secret | `projects/unicon-494419/secrets/yisbeta-db-password/versions/latest` |

### Test DB Connectivity from Cloud Run

```bash
curl -s "https://agentic-rag-chat-6slvib5z6a-uc.a.run.app/healthz/db"
# Expected: {"db_connectivity": {"yisbeta_relay_vm": "REACHABLE (10.128.0.3:1433)", ...}}
```

---

## Secret Manager

| Secret name | Used for |
|---|---|
| `yisbeta-db-password` | YISBeta SQL Server password (`3vAnalysts2` user) |

```bash
# Grant Cloud Run service account access
gcloud secrets add-iam-policy-binding yisbeta-db-password \
  --project=unicon-494419 \
  --member="serviceAccount:SERVICE_ACCOUNT@unicon-494419.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Connections Config

All DB connections are defined in `connections.json` at the repo root. Currently configured:

| Alias | Host | Type | Notes |
|---|---|---|---|
| `yisbeta_relay_vm` | `10.128.0.3:1433` | mssql | **Default** — via VPN relay VM |
| `yisbeta` | `10.0.0.22:1433` | mssql | Direct (unreachable from Cloud Run) |
| `yisbeta_tunnel` | `127.0.0.1:14333` | mssql | Local dev via Docker tunnel |
| `local_pg` | `127.0.0.1:5432` | postgres | Cloud SQL PostgreSQL |

---

## Known Issues & Fixes Applied

### 1. VPN Route Hijacking (FIXED)
**Problem:** Docker `--network=host` container adds `10.0.0.0/8 via ppp0`. Reply packets to Cloud Run's `10.8.0.2` were routed into the VPN tunnel instead of back to GCP VPC — causing TCP timeouts.  
**Fix:** `ip route add 10.8.0.0/28 via 10.128.0.1 dev ens4` (persisted via rc.local + systemd).

### 2. `SELECT DISTINCT TOP N` Syntax Error (FIXED)
**Problem:** The `_inject_limit_if_missing` function in `agent.py` was transforming `SELECT DISTINCT col FROM tbl` into `SELECT TOP 200 DISTINCT col FROM tbl` — invalid T-SQL syntax. SQL Server requires `DISTINCT` before `TOP`.  
**Fix:** Added DISTINCT-aware branch in `_inject_limit_if_missing` that produces `SELECT DISTINCT TOP N`.

### 3. `--set-env-vars` Wipes All Existing Vars (KNOWN GOTCHA)
Always use `--update-env-vars` when changing Cloud Run env vars. Using `--set-env-vars` replaces the entire environment and will remove vars set in previous deploys.

### 4. Gemini Model `gemini-3.1-flash-lite-preview` (OBSOLETE)
Use `gemini-2.5-flash-lite` — the preview model returned 404 on Vertex AI.

---

## Useful Commands

```bash
# Tail Cloud Run logs live
gcloud run services logs tail agentic-rag-chat --region=us-central1

# Describe current Cloud Run revision
gcloud run services describe agentic-rag-chat \
  --region=us-central1 \
  --format="yaml(spec.template.spec.containers[0].env)"

# List Cloud Run revisions
gcloud run revisions list \
  --service=agentic-rag-chat \
  --region=us-central1

# Check VPC connector
gcloud compute networks vpc-access connectors describe ai-factory-connector \
  --region=us-central1 \
  --project=unicon-494419

# SSH to relay VM and check VPN in one shot
gcloud compute ssh yisbeta-vpn-relay \
  --project=unicon-494419 --zone=us-central1-f \
  --tunnel-through-iap --quiet \
  --command="sudo docker exec youngsinc-tunnel ip addr show ppp0 | grep inet"
```
