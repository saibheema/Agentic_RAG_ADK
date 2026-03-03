#!/bin/bash
# ==============================================================================
# entrypoint.sh — IKEv2 IPSec VPN setup + SQL Server port forward
#
# Required environment variables (passed at docker run time):
#   VPN_SERVER   — hostname or IP of VPN server (e.g. remote.youngsinc.com)
#   VPN_PSK      — IPSec Pre-Shared Key
#   TARGET_HOST  — internal IP to forward (e.g. 10.0.0.22)
#   TARGET_PORT  — internal port to forward (default: 1433)
#
# Protocol: IKEv2, AES-256, SHA-512, DH Group 20 (ecp384 in strongSwan)
# No L2TP/PPP/xl2tpd needed — pure IKEv2 site-to-site.
# ==============================================================================
set -euo pipefail

VPN_SERVER="${VPN_SERVER:?VPN_SERVER is required}"
VPN_PSK="${VPN_PSK:?VPN_PSK is required}"
TARGET_HOST="${TARGET_HOST:-10.0.0.22}"
TARGET_PORT="${TARGET_PORT:-1433}"

echo "[vpn] Configuring IKEv2 IPSec (strongSwan)..."

# ── ipsec.conf ────────────────────────────────────────────────────────────────
cat > /etc/ipsec.conf <<EOF
config setup
    uniqueids=never
    charondebug="ike 2, knl 1, cfg 1"

conn youngsinc
    keyexchange=ikev2
    authby=secret
    auto=start
    left=%defaultroute
    leftsubnet=0.0.0.0/0
    right=${VPN_SERVER}
    rightsubnet=10.0.0.0/16
    ike=aes256-sha512-ecp384!
    esp=aes256-sha512-ecp384!
    ikelifetime=86400s
    keylife=3600s
    dpdaction=restart
    dpddelay=30s
    dpdtimeout=120s
EOF

# ── ipsec.secrets ─────────────────────────────────────────────────────────────
cat > /etc/ipsec.secrets <<EOF
%any ${VPN_SERVER} : PSK "${VPN_PSK}"
EOF
chmod 600 /etc/ipsec.secrets

echo "[vpn] Starting strongSwan (IKEv2 charon daemon)..."
ipsec start
sleep 3

echo "[vpn] Initiating IKEv2 tunnel to ${VPN_SERVER}..."
ipsec up youngsinc || true

# ── Wait for IKEv2 SA to be ESTABLISHED ───────────────────────────────────────
MAX_IPSEC_WAIT=30
i=0
while ! ipsec statusall 2>/dev/null | grep -q "ESTABLISHED"; do
    sleep 1
    i=$((i + 1))
    if [ $i -ge $MAX_IPSEC_WAIT ]; then
        echo "[vpn] ERROR: IKEv2 SA did not establish after ${MAX_IPSEC_WAIT}s"
        echo "[vpn] --- ipsec statusall ---"
        ipsec statusall 2>/dev/null || true
        exit 1
    fi
    echo "[vpn] Waiting for IKEv2 SA... (${i}/${MAX_IPSEC_WAIT})"
done
echo "[vpn] IKEv2 SA ESTABLISHED!"

# ── Verify SQL Server is reachable through tunnel ──────────────────────────────
echo "[vpn] Testing connectivity to ${TARGET_HOST}:${TARGET_PORT}..."
for attempt in 1 2 3 4 5; do
    if timeout 5 bash -c "echo >/dev/tcp/${TARGET_HOST}/${TARGET_PORT}" 2>/dev/null; then
        echo "[vpn] SUCCESS: ${TARGET_HOST}:${TARGET_PORT} is reachable!"
        break
    fi
    echo "[vpn] Retrying target connectivity... (${attempt}/5)"
    sleep 3
done

# ── Start socat port forward ───────────────────────────────────────────────────
echo "[vpn] Forwarding 0.0.0.0:1433 → ${TARGET_HOST}:${TARGET_PORT}"
exec socat TCP-LISTEN:1433,fork,reuseaddr TCP:${TARGET_HOST}:${TARGET_PORT}


VPN_SERVER="${VPN_SERVER:?VPN_SERVER is required}"
VPN_PSK="${VPN_PSK:?VPN_PSK is required}"
VPN_USER="${VPN_USER:?VPN_USER is required}"
VPN_PASS="${VPN_PASS:?VPN_PASS is required}"
TARGET_HOST="${TARGET_HOST:-10.0.0.22}"
TARGET_PORT="${TARGET_PORT:-1433}"

echo "[vpn] Configuring IPSec (strongSwan)..."

# ── ipsec.conf ────────────────────────────────────────────────────────────────
cat > /etc/ipsec.conf <<EOF
config setup
    uniqueids=never
    charondebug="ike 2, knl 0, cfg 1, net 2"

conn youngsinc
    authby=secret
    auto=add
    keyexchange=ikev1
    left=%defaultroute
    leftprotoport=17/%any
    right=${VPN_SERVER}
    rightid=%any
    rightprotoport=17/1701
    type=transport
    esp=3des-sha1,aes256-sha256,aes256-sha1,aes128-sha256,aes128-sha1!
    ike=3des-sha1-modp1024,aes256-sha256-modp2048,aes256-sha1-modp2048,aes128-sha256-modp2048,aes128-sha1-modp2048!
    ikelifetime=86400s
    keylife=3600s
    dpdaction=restart
    dpddelay=30s
    dpdtimeout=120s
EOF

# ── ipsec.secrets ─────────────────────────────────────────────────────────────
cat > /etc/ipsec.secrets <<EOF
%any ${VPN_SERVER} : PSK "${VPN_PSK}"
EOF
chmod 600 /etc/ipsec.secrets

# ── xl2tpd.conf ───────────────────────────────────────────────────────────────
mkdir -p /etc/xl2tpd
cat > /etc/xl2tpd/xl2tpd.conf <<EOF
[global]
port = 1701

[lac youngsinc]
lns = ${VPN_SERVER}
ppp debug = yes
pppoptfile = /etc/ppp/options.youngsinc
length bit = yes
EOF

# ── PPP options ───────────────────────────────────────────────────────────────
mkdir -p /etc/ppp
cat > /etc/ppp/options.youngsinc <<EOF
ipcp-accept-local
ipcp-accept-remote
refuse-eap
require-mschap-v2
noccp
noauth
mtu 1280
mru 1280
noipdefault
defaultroute
usepeerdns
connect-delay 5000
name ${VPN_USER}
password ${VPN_PASS}
EOF
chmod 600 /etc/ppp/options.youngsinc

# ── CHAP secrets ─────────────────────────────────────────────────────────────
cat > /etc/ppp/chap-secrets <<EOF
"${VPN_USER}" * "${VPN_PASS}" *
EOF
chmod 600 /etc/ppp/chap-secrets

echo "[vpn] Starting IPSec..."
ipsec start
sleep 3

echo "[vpn] Initiating IKEv1 handshake with ${VPN_SERVER}..."
ipsec up youngsinc || true   # 'true' so we can check status ourselves below

# Wait for IPSec SA to be ESTABLISHED before starting L2TP
MAX_IPSEC_WAIT=30
i=0
while ! ipsec statusall 2>/dev/null | grep -q "ESTABLISHED"; do
    sleep 1
    i=$((i + 1))
    if [ $i -ge $MAX_IPSEC_WAIT ]; then
        echo "[vpn] ERROR: IPSec SA did not establish after ${MAX_IPSEC_WAIT}s"
        echo "[vpn] --- ipsec statusall ---"
        ipsec statusall 2>/dev/null || true
        echo "[vpn] --- dmesg (last 20 lines) ---"
        dmesg 2>/dev/null | tail -20 || true
        exit 1
    fi
    echo "[vpn] Waiting for IPSec SA... (${i}/${MAX_IPSEC_WAIT})"
done
echo "[vpn] IPSec SA ESTABLISHED!"

echo "[vpn] Starting xl2tpd and sending L2TP connect command..."
mkdir -p /var/run/xl2tpd

# xl2tpd detects /var/run/pluto.ctl (Openswan/Libreswan SAref socket) and if present,
# automatically switches to userspace mode ("force userspace=yes") instead of kernel L2TP.
# Kernel L2TP mode bypasses the IPSec SPD policies inside Docker and causes:
#   "L2TP control traffic apply rule not found"
# Creating this file triggers the fallback to userspace UDP sockets that ARE encrypted.
touch /var/run/pluto.ctl

xl2tpd -D &
sleep 2

# Send L2TP connect command via xl2tpd control pipe
echo "c youngsinc" > /var/run/xl2tpd/l2tp-control
sleep 5

# ── Wait for ppp0 interface ────────────────────────────────────────────────────
echo "[vpn] Waiting for ppp0 interface..."
MAX_WAIT=60
i=0
while ! ip link show ppp0 &>/dev/null; do
    sleep 1
    i=$((i + 1))
    if [ $i -ge $MAX_WAIT ]; then
        echo "[vpn] ERROR: ppp0 did not come up after ${MAX_WAIT}s"
        echo "[vpn] IPSec status:"
        ipsec statusall 2>/dev/null || true
        exit 1
    fi
done

PPP_IP=$(ip addr show ppp0 | grep 'inet ' | awk '{print $2}' | cut -d/ -f1)
PPP_PEER=$(ip addr show ppp0 | grep 'peer ' | awk '{print $4}' | cut -d/ -f1)
echo "[vpn] ppp0 is up! Local tunnel IP: ${PPP_IP}, Peer: ${PPP_PEER}"

# Add route to internal network via VPN peer (server does not push routes automatically)
echo "[vpn] Adding route: 10.0.0.0/8 via ${PPP_PEER} dev ppp0"
ip route add 10.0.0.0/8 via "${PPP_PEER}" dev ppp0 2>/dev/null || \
ip route add 10.0.0.0/8 dev ppp0 2>/dev/null || \
echo "[vpn] WARNING: could not add route (may already exist)"

echo "[vpn] Tunnel connected. Target: ${TARGET_HOST}:${TARGET_PORT}"

# ── Verify SQL Server is reachable through tunnel ──────────────────────────────
echo "[vpn] Testing connectivity to ${TARGET_HOST}:${TARGET_PORT}..."
if ! timeout 10 bash -c "echo >/dev/tcp/${TARGET_HOST}/${TARGET_PORT}" 2>/dev/null; then
    echo "[vpn] WARNING: Cannot reach ${TARGET_HOST}:${TARGET_PORT} yet (may need a moment)"
else
    echo "[vpn] SUCCESS: ${TARGET_HOST}:${TARGET_PORT} is reachable!"
fi

# ── Start socat port forward ───────────────────────────────────────────────────
echo "[vpn] Forwarding 0.0.0.0:1433 → ${TARGET_HOST}:${TARGET_PORT}"
exec socat TCP-LISTEN:1433,fork,reuseaddr TCP:${TARGET_HOST}:${TARGET_PORT}
