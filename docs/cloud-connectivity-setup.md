# Cloud Connectivity Setup — GCP VPN, VPC Connector & SQL Server

## Table of Contents
1. [Architecture Overview](#architecture-overview)
2. [Prerequisites](#prerequisites)
3. [Step 1 — Reserve a Static External IP](#step-1--reserve-a-static-external-ip)
4. [Step 2 — Create the Classic VPN Gateway](#step-2--create-the-classic-vpn-gateway)
5. [Step 3 — Create the VPN Tunnel (IKEv2)](#step-3--create-the-vpn-tunnel-ikev2)
6. [Step 4 — Add a Route for the Client Subnet](#step-4--add-a-route-for-the-client-subnet)
7. [Step 5 — Create Firewall Rules](#step-5--create-firewall-rules)
8. [Step 6 — Create a Serverless VPC Access Connector](#step-6--create-a-serverless-vpc-access-connector)
9. [Step 7 — Configure Cloud Run to Use the Connector](#step-7--configure-cloud-run-to-use-the-connector)
10. [Step 8 — Cloud SQL (PostgreSQL)](#step-8--cloud-sql-postgresql)
11. [Step 9 — Secret Manager for Credentials](#step-9--secret-manager-for-credentials)
12. [Local Dev — Docker IKEv2 Tunnel](#local-dev--docker-ikev2-tunnel)
13. [Security Assessment](#security-assessment)
14. [Troubleshooting Reference](#troubleshooting-reference)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Google Cloud (us-central1)                                         │
│                                                                     │
│  ┌───────────────┐     VPC Connector       ┌────────────────────┐  │
│  │  Cloud Run    │ ──────────────────────► │  default VPC       │  │
│  │  agentic-rag  │   youngsinc-connector    │  172.16.0.0/28     │  │
│  └───────────────┘   (serverless egress)   └────────┬───────────┘  │
│                                                     │              │
│                                            ┌────────▼───────────┐  │
│                                            │  Classic VPN GW    │  │
│                                            │  youngsinc-vpn-gw  │  │
│                                            │  IP: 34.10.184.224 │  │
│                                            └────────┬───────────┘  │
└─────────────────────────────────────────────────────┼─────────────┘
                                                      │ IKEv2 IPSec
                                                      │ AES-256-GCM
                                                      │ SHA-512
                                          ┌───────────▼────────────┐
                                          │  Youngsinc HQ VPN GW   │
                                          │  72.240.11.135         │
                                          │  remote.youngsinc.com  │
                                          └───────────┬────────────┘
                                                      │ LAN: 10.0.0.0/16
                                          ┌───────────▼────────────┐
                                          │  YISBeta SQL Server    │
                                          │  10.0.0.22:1433        │
                                          └────────────────────────┘

  Cloud Run also connects to Cloud SQL via Auth Proxy (no VPN needed):
  ┌───────────────┐   Cloud SQL Auth Proxy   ┌──────────────────────┐
  │  Cloud Run    │ ────────────────────────►│  agentic-rag-pg      │
  │  agentic-rag  │   (IAM authentication)   │  PostgreSQL 15       │
  └───────────────┘                          │  unicon-494419:      │
                                             │  us-central1:        │
                                             │  agentic-rag-pg      │
                                             └──────────────────────┘
```

**Traffic paths:**

| Source | Destination | Path |
|--------|-------------|------|
| Cloud Run | YISBeta SQL Server (10.0.0.22:1433) | VPC Connector → VPN Tunnel → Youngsinc LAN |
| Cloud Run | Cloud SQL PostgreSQL | Cloud SQL Auth Proxy (IAM, no VPN) |
| Local dev | YISBeta SQL Server | Docker IKEv2 tunnel → localhost:14333 |

---

## Prerequisites

- GCP project: `unicon-494419`
- `gcloud` CLI authenticated: `gcloud auth login`
- Youngsinc VPN peer details:
  - Peer IP: `72.240.11.135` (remote.youngsinc.com)
  - Pre-Shared Key (PSK): stored locally only — never committed to git
  - Client LAN subnet: `10.0.0.0/16`
  - Protocol: IKEv2, AES-256-GCM, SHA-512, DH Group 20

---

## Step 1 — Reserve a Static External IP

```bash
gcloud compute addresses create youngsinc-vpn-ip \
  --project=unicon-494419 \
  --region=us-central1

# Verify
gcloud compute addresses describe youngsinc-vpn-ip \
  --region=us-central1 \
  --format="value(address)"
# → 34.10.184.224
```

**Why**: The Cloud-side VPN peer IP must be static so Youngsinc can pin it in their firewall/VPN config.

---

## Step 2 — Create the Classic VPN Gateway

```bash
gcloud compute vpn-gateways create youngsinc-vpn-gateway \
  --project=unicon-494419 \
  --region=us-central1 \
  --network=default
```

**Why Classic VPN (not HA VPN)**: Youngsinc's end is a single-peer policy-based VPN. Classic VPN supports route-based and policy-based peers. HA VPN requires two tunnels and BGP on both sides.

Create the three forwarding rules needed for IPSec Classic VPN:

```bash
# ESP (IP protocol 50) — encrypted IPSec payload
gcloud compute forwarding-rules create youngsinc-fr-esp \
  --project=unicon-494419 --region=us-central1 \
  --ip-protocol=ESP --address=youngsinc-vpn-ip \
  --target-vpn-gateway=youngsinc-vpn-gateway

# IKE UDP 500 — key exchange
gcloud compute forwarding-rules create youngsinc-fr-udp500 \
  --project=unicon-494419 --region=us-central1 \
  --ip-protocol=UDP --ports=500 --address=youngsinc-vpn-ip \
  --target-vpn-gateway=youngsinc-vpn-gateway

# NAT-T UDP 4500 — IKEv2 NAT traversal
gcloud compute forwarding-rules create youngsinc-fr-udp4500 \
  --project=unicon-494419 --region=us-central1 \
  --ip-protocol=UDP --ports=4500 --address=youngsinc-vpn-ip \
  --target-vpn-gateway=youngsinc-vpn-gateway
```

---

## Step 3 — Create the VPN Tunnel (IKEv2)

```bash
gcloud compute vpn-tunnels create youngsinc-tunnel \
  --project=unicon-494419 \
  --region=us-central1 \
  --vpn-gateway=youngsinc-vpn-gateway \
  --peer-address=72.240.11.135 \
  --shared-secret='<PSK — stored in run_vpn_tunnel.sh, not in git>' \
  --ike-version=2 \
  --local-traffic-selector=0.0.0.0/0 \
  --remote-traffic-selector=0.0.0.0/0

# Verify status
gcloud compute vpn-tunnels describe youngsinc-tunnel \
  --region=us-central1 \
  --format="value(status,detailedStatus)"
# → ESTABLISHED
```

**IKE parameters negotiated with Youngsinc:**

| Parameter | Value |
|-----------|-------|
| IKE version | IKEv2 |
| Encryption | AES-256-GCM |
| Integrity | SHA-512 |
| DH Group | Group 20 (ECP-384) |
| Authentication | Pre-Shared Key (PSK) |

---

## Step 4 — Add a Route for the Client Subnet

```bash
gcloud compute routes create youngsinc-route-10x \
  --project=unicon-494419 \
  --network=default \
  --destination-range=10.0.0.0/16 \
  --next-hop-vpn-tunnel=youngsinc-tunnel \
  --next-hop-vpn-tunnel-region=us-central1 \
  --priority=1000
```

This tells GCP's VPC: _any traffic destined for 10.0.0.0/16 (Youngsinc LAN) should go through the VPN tunnel_.

---

## Step 5 — Create Firewall Rules

```bash
# Allow inbound traffic from Youngsinc LAN to GCP resources
gcloud compute firewall-rules create allow-youngsinc-vpn-ingress \
  --project=unicon-494419 \
  --network=default \
  --direction=INGRESS \
  --source-ranges=10.0.0.0/16 \
  --allow=tcp,udp,icmp \
  --priority=1000 \
  --description="Allow traffic from Youngsinc LAN via VPN"
```

> **Note**: Outbound (egress) traffic from GCP VMs/Cloud Run to 10.0.0.0/16 is allowed by default in GCP VPCs. Only ingress rules are needed.

---

## Step 6 — Create a Serverless VPC Access Connector

Cloud Run is serverless and does **not** live inside the VPC by default. A VPC Access Connector bridges Cloud Run into the VPC so it can use the VPN tunnel.

```bash
gcloud services enable vpcaccess.googleapis.com --project=unicon-494419

gcloud compute networks vpc-access connectors create youngsinc-connector \
  --project=unicon-494419 \
  --region=us-central1 \
  --network=default \
  --range=172.16.0.0/28 \
  --min-instances=2 \
  --max-instances=3 \
  --machine-type=f1-micro

# Verify
gcloud compute networks vpc-access connectors describe youngsinc-connector \
  --region=us-central1 \
  --format="value(state)"
# → READY
```

**CIDR choice for connector**: `172.16.0.0/28` was chosen because:
- `10.0.0.0/8` is taken by Youngsinc LAN
- `10.8.0.0/28` conflicted with an existing GCP subnet
- `172.16.0.0/28` was free in RFC 1918 space

The `/28` provides 16 IPs, sufficient for the 2–3 connector instances.

---

## Step 7 — Configure Cloud Run to Use the Connector

When deploying (or updating) the Cloud Run service, specify the connector and egress mode:

```bash
gcloud run deploy agentic-rag \
  --project=unicon-494419 \
  --region=us-central1 \
  --source . \
  --vpc-connector=youngsinc-connector \
  --vpc-egress=private-ranges-only \
  --allow-unauthenticated
```

`--vpc-egress=private-ranges-only` means only RFC 1918 traffic (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16) goes through the VPC connector/VPN. Public internet traffic (e.g. Gemini API) goes directly — no performance penalty.

**To switch the default database without redeploying:**

```bash
# Switch to Cloud SQL PostgreSQL
gcloud run services update agentic-rag \
  --region=us-central1 \
  --update-env-vars="DB_DEFAULT_ALIAS=local_pg"

# Switch back to YISBeta SQL Server
gcloud run services update agentic-rag \
  --region=us-central1 \
  --update-env-vars="DB_DEFAULT_ALIAS=yisbeta"
```

---

## Step 8 — Cloud SQL (PostgreSQL)

Cloud SQL is accessed via the **Cloud SQL Auth Proxy** — an IAM-authenticated, TLS-encrypted sidecar that Cloud Run manages automatically (no VPN required for Cloud SQL).

```bash
# Create the instance
gcloud sql instances create agentic-rag-pg \
  --project=unicon-494419 \
  --region=us-central1 \
  --database-version=POSTGRES_15 \
  --tier=db-f1-micro

# Create the database
gcloud sql databases create agentic_rag \
  --instance=agentic-rag-pg

# Create the app user
gcloud sql users create app_user \
  --instance=agentic-rag-pg \
  --password='<generated-password>'

# Store the password in Secret Manager
gcloud secrets create local-pg-password --replication-policy=automatic
echo -n '<generated-password>' | gcloud secrets versions add local-pg-password --data-file=-
```

In `connections.json`, the Cloud SQL entry uses `instance_connection_name` so the Auth Proxy knows which instance to connect to:

```json
{
  "alias": "local_pg",
  "db_type": "postgres",
  "host": "127.0.0.1",
  "port": 5432,
  "database": "agentic_rag",
  "user": "app_user",
  "password_secret": "projects/unicon-494419/secrets/local-pg-password/versions/latest",
  "instance_connection_name": "unicon-494419:us-central1:agentic-rag-pg"
}
```

---

## Step 9 — Secret Manager for Credentials

All database passwords are stored in GCP Secret Manager — **never in environment variables or config files in git**.

```bash
# Grant the Cloud Run service account access to secrets
SA="664984131730-compute@developer.gserviceaccount.com"

gcloud secrets add-iam-policy-binding yisbeta-db-password \
  --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding local-pg-password \
  --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
```

At runtime, `connections.py` calls `google.cloud.secretmanager` to fetch the latest version. The service account's identity is automatically provided by Cloud Run's metadata server — no keys or service account JSON files needed.

---

## Local Dev — Docker IKEv2 Tunnel

For local development, the VPN tunnel is replicated inside a Docker container using **strongSwan** (IKEv2).

```
┌──────────────────────────────────────┐
│  MacBook (localhost)                 │
│                                      │
│  agentic-rag agent  → localhost:14333│
│                       ↓              │
│  ┌─────────────────────────────────┐ │
│  │  Docker: youngsinc-tunnel       │ │
│  │  strongSwan IKEv2 + socat       │ │
│  │  PORT 14333 mapped to ppp0:1433 │ │
│  └────────────┬────────────────────┘ │
└───────────────┼──────────────────────┘
                │ IKEv2 (UDP 500/4500, ESP)
        72.240.11.135 → 10.0.0.22:1433
```

**Setup:**

```bash
# 1. Copy the template (fill in credentials — NEVER commit run_vpn_tunnel.sh)
cp scripts/docker_vpn/run_vpn_tunnel.sh.example scripts/docker_vpn/run_vpn_tunnel.sh
# Edit: VPN_SERVER, VPN_PSK, TARGET_HOST, TARGET_PORT

# 2. Start the tunnel
cd scripts/docker_vpn && bash run_vpn_tunnel.sh

# 3. Verify
nc -zv localhost 14333 && echo "CONNECTED"

# 4. In connections.json, use alias: yisbeta_tunnel
```

The `yisbeta_tunnel` connection is marked `"local_only": true` so it is hidden in the Cloud Run UI automatically — it only appears when the app is accessed via localhost.

**Stop the tunnel:**
```bash
bash scripts/docker_vpn/stop_vpn_tunnel.sh
```

---

## Security Assessment

### ✅ What is Secured

| Control | Detail |
|---------|--------|
| **VPN encryption** | IKEv2, AES-256-GCM (AEAD), SHA-512, DH Group 20 (ECP-384 = 192-bit equivalent) — NIST-recommended post-2025 algorithms |
| **Pre-Shared Key** | PSK never stored in git; gitignored in `run_vpn_tunnel.sh`; CI/CD has no access to it |
| **DB passwords** | All passwords in GCP Secret Manager with version pinning (`/versions/latest`) |
| **Secret access IAM** | Only the Cloud Run service account (`664984131730-compute@developer.gserviceaccount.com`) holds `roles/secretmanager.secretAccessor` — no human accounts |
| **Cloud SQL auth** | Auth Proxy uses IAM identity — no network-level SQL password transmitted on the wire |
| **Cloud Run egress** | `vpc-egress=private-ranges-only` — private traffic via VPN, public traffic (Gemini) direct. No traffic mixing |
| **Branch protection** | `main` requires 1 PR review + enforce_admins — no direct pushes |
| **No hardcoded secrets** | `connections.json` references secret paths, not values. `.env` is gitignored |

### ⚠️ Residual Risks and Mitigations

| Risk | Severity | Current State | Recommended Mitigation |
|------|----------|---------------|------------------------|
| **PSK is static** | Medium | PSK shared with Youngsinc admin out-of-band | Rotate PSK quarterly; consider migrating to certificate-based IKEv2 |
| **SQL Server user `3vAnalysts2` scope** | Medium | Unknown if least-privilege | Confirm with Youngsinc that the account is read-only or limited to required tables |
| **Cloud Run is `--allow-unauthenticated`** | Medium | Open to public internet (required for UI) | Add Cloud Armor WAF, or Identity-Aware Proxy (IAP) for authenticated access |
| **Classic VPN (single tunnel)** | Low | Single point of failure if tunnel drops | Migrate to HA VPN with two tunnels for production SLA |
| **VPC connector CIDR is RFC 1918** | Low | `172.16.0.0/28` not overlapping anything currently | Document if network is expanded |
| **Secret versions set to `latest`** | Low | Auto-picks latest version | Pin to specific version IDs in production to prevent accidental rotation breakage |
| **Local dev PSK on developer Macs** | Low | In gitignored `run_vpn_tunnel.sh` only | Use 1Password/Vault for developer secret distribution instead of sharing PSK directly |

### Network Flow Security Summary

```
User Browser
    │  HTTPS (TLS 1.3)
    ▼
Cloud Run (HTTPS endpoint, Google-managed cert)
    │  IAM + Secret Manager API (TLS, IAM-authenticated)
    ▼
Secret Manager (fetches DB password)
    │
    ├── For YISBeta ──► VPC Connector ──► Classic VPN (IKEv2, AES-256-GCM) ──► SQL Server 1433
    │                   (RFC 1918 only)    (encrypted tunnel)                  (TDS/SQL payload)
    │
    └── For Cloud SQL ──► Cloud SQL Auth Proxy (IAM + TLS) ──► PostgreSQL
                          (mTLS, Google-internal)
```

---

## Troubleshooting Reference

### VPN Tunnel Down

```bash
# Check tunnel status
gcloud compute vpn-tunnels describe youngsinc-tunnel \
  --region=us-central1 \
  --format="value(status,detailedStatus)"

# Common statuses:
# ESTABLISHED — working
# WAITING_FOR_FULL_CONFIG — IKE SA up but no traffic selectors agreed yet
# FIRST_HANDSHAKE — negotiating; wait 60s and re-check
# NO_INCOMING_PACKETS — firewall at Youngsinc may be blocking UDP 500/4500
```

### Cloud Run Cannot Reach 10.0.0.22

```bash
# Verify connector is READY
gcloud compute networks vpc-access connectors describe youngsinc-connector \
  --region=us-central1 --format="value(state)"

# Verify Cloud Run is using the connector
gcloud run services describe agentic-rag \
  --region=us-central1 \
  --format="value(spec.template.metadata.annotations)"
# Should show run.googleapis.com/vpc-access-connector: youngsinc-connector
```

### Secret Access Denied

```bash
# Test secret access manually
gcloud secrets versions access latest \
  --secret=yisbeta-db-password \
  --project=unicon-494419 \
  --impersonate-service-account=664984131730-compute@developer.gserviceaccount.com
```

### Cloud SQL Connection Refused

```bash
# Check instance is running
gcloud sql instances describe agentic-rag-pg \
  --format="value(state,backendType)"

# Check app_user exists
gcloud sql users list --instance=agentic-rag-pg
```

---

*Document created: March 2026 — unicon-494419 / Agentic RAG project. Maintained in `docs/cloud-connectivity-setup.md`.*
