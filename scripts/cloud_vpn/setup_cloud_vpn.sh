#!/bin/bash
# ==============================================================================
# setup_cloud_vpn.sh — Create GCP Classic VPN to Youngsinc network
# ⚠️  GITIGNORED — contains credentials
#
# Run ONCE to set up the GCP-side VPN so Cloud Run can reach 10.0.0.0/16
# directly (without the Docker tunnel used for local dev).
#
# Protocol : IKEv2
# Cipher   : AES-256 / SHA-512 / DH Group 20 (ecp384)
# Remote   : remote.youngsinc.com  →  10.0.0.0/16
# ==============================================================================
set -euo pipefail

PROJECT="unicon-494419"
REGION="us-central1"
NETWORK="default"
VPN_PSK='!RareDay33?ClearGate49%'
VPN_SERVER="remote.youngsinc.com"
REMOTE_SUBNET="10.0.0.0/16"

GW_NAME="youngsinc-vpn-gateway"
TUNNEL_NAME="youngsinc-tunnel"
ROUTE_NAME="youngsinc-route"
IP_NAME="youngsinc-vpn-ip"

echo "==> Resolving ${VPN_SERVER} to IP..."
PEER_IP=$(python3 -c "import socket; print(socket.gethostbyname('${VPN_SERVER}'))")
echo "    Peer IP: ${PEER_IP}"

# ── 1. Reserve a static external IP for our GCP VPN gateway ──────────────────
echo "==> Reserving static external IP (${IP_NAME})..."
gcloud compute addresses create "${IP_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT}" 2>/dev/null || echo "    (already exists, continuing)"

GW_IP=$(gcloud compute addresses describe "${IP_NAME}" \
    --region="${REGION}" --project="${PROJECT}" \
    --format='value(address)')
echo "    GCP VPN gateway IP: ${GW_IP}"
echo ""
echo "  *** SEND THIS IP TO YOUNGSINC ***"
echo "  They need to add ${GW_IP} as a peer on their VPN gateway."
echo ""

# ── 2. Create target VPN gateway ─────────────────────────────────────────────
echo "==> Creating target VPN gateway (${GW_NAME})..."
gcloud compute target-vpn-gateways create "${GW_NAME}" \
    --network="${NETWORK}" \
    --region="${REGION}" \
    --project="${PROJECT}" 2>/dev/null || echo "    (already exists, continuing)"

# ── 3. Create forwarding rules (Classic VPN needs ESP + UDP 500 + UDP 4500) ──
echo "==> Creating forwarding rules..."
gcloud compute forwarding-rules create "${GW_NAME}-esp" \
    --project="${PROJECT}" --region="${REGION}" \
    --ip-protocol=ESP --address="${GW_IP}" \
    --target-vpn-gateway="${GW_NAME}" 2>/dev/null || echo "    ESP rule exists"

gcloud compute forwarding-rules create "${GW_NAME}-udp500" \
    --project="${PROJECT}" --region="${REGION}" \
    --ip-protocol=UDP --ports=500 --address="${GW_IP}" \
    --target-vpn-gateway="${GW_NAME}" 2>/dev/null || echo "    UDP 500 rule exists"

gcloud compute forwarding-rules create "${GW_NAME}-udp4500" \
    --project="${PROJECT}" --region="${REGION}" \
    --ip-protocol=UDP --ports=4500 --address="${GW_IP}" \
    --target-vpn-gateway="${GW_NAME}" 2>/dev/null || echo "    UDP 4500 rule exists"

# ── 4. Create VPN tunnel (IKEv2) ─────────────────────────────────────────────
echo "==> Creating VPN tunnel (IKEv2, AES-256, SHA-512, DH Group 20)..."
gcloud compute vpn-tunnels create "${TUNNEL_NAME}" \
    --project="${PROJECT}" \
    --region="${REGION}" \
    --peer-address="${PEER_IP}" \
    --target-vpn-gateway="${GW_NAME}" \
    --ike-version=2 \
    --shared-secret="${VPN_PSK}" \
    --local-traffic-selector=0.0.0.0/0 \
    --remote-traffic-selector="${REMOTE_SUBNET}" 2>/dev/null || echo "    (already exists, continuing)"

# ── 5. Add route for Youngsinc internal network ───────────────────────────────
echo "==> Adding route ${REMOTE_SUBNET} → ${TUNNEL_NAME}..."
gcloud compute routes create "${ROUTE_NAME}" \
    --project="${PROJECT}" \
    --network="${NETWORK}" \
    --dest-range="${REMOTE_SUBNET}" \
    --next-hop-vpn-tunnel="${TUNNEL_NAME}" \
    --next-hop-vpn-tunnel-region="${REGION}" 2>/dev/null || echo "    (already exists, continuing)"

# ── 6. Create Serverless VPC Access connector (Cloud Run → VPN egress) ────────
CONNECTOR_NAME="youngsinc-connector"
CONNECTOR_RANGE="172.16.0.0/28"   # isolated /28 — not used by any subnet

echo "==> Creating Serverless VPC Access connector (${CONNECTOR_NAME})..."
gcloud compute networks vpc-access connectors create "${CONNECTOR_NAME}" \
    --network="${NETWORK}" \
    --region="${REGION}" \
    --range="${CONNECTOR_RANGE}" \
    --min-instances=2 \
    --max-instances=3 \
    --machine-type=e2-micro \
    --project="${PROJECT}" 2>/dev/null || echo "    (already exists, continuing)"

gcloud compute networks vpc-access connectors describe "${CONNECTOR_NAME}" \
    --region="${REGION}" --project="${PROJECT}" \
    --format="table(name,state,ipCidrRange)" 2>&1

# ── 7. Status check ───────────────────────────────────────────────────────────
echo ""
echo "==> Tunnel status:"
gcloud compute vpn-tunnels describe "${TUNNEL_NAME}" \
    --region="${REGION}" --project="${PROJECT}" \
    --format="table(name, status, detailedStatus)"

echo ""
echo "======================================================================"
echo "  GCP Cloud VPN setup complete!"
echo ""
echo "  GCP gateway IP  : ${GW_IP}   ← give this to Youngsinc"
echo "  Peer (Youngsinc): ${PEER_IP} (${VPN_SERVER})"
echo "  Remote subnet   : ${REMOTE_SUBNET}"
echo "  Tunnel          : ${TUNNEL_NAME}"
echo "  VPC Connector   : ${CONNECTOR_NAME} (${CONNECTOR_RANGE})"
echo ""
echo "  Next steps for Cloud Run deployment:"
echo "  1. Add --vpc-connector=youngsinc-connector to 'gcloud run deploy'"
echo "  2. Add --vpc-egress=private-ranges-only"
echo "  3. Use alias 'yisbeta' in connections.json (host 10.0.0.22:1433)"
echo "======================================================================"
