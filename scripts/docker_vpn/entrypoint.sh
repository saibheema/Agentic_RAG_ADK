#!/bin/bash
# ==============================================================================
# entrypoint.sh — Persistent L2TP/IPSec VPN relay with auto-reconnect
#
# Required environment variables (passed at docker run time):
#   VPN_SERVER   — hostname or IP of VPN server (e.g. remote.youngsinc.com)
#   VPN_PSK      — IPSec Pre-Shared Key
#   VPN_USER     — PPP/L2TP username
#   VPN_PASS     — PPP/L2TP password
#   TARGET_HOST  — internal IP to forward (e.g. 10.0.0.22)
#   TARGET_PORT  — internal port to forward (default: 1433)
#
# Optional:
#   LISTEN_PORT      — port to expose on container (default: 1433)
#   RECONNECT_DELAY  — seconds to wait before reconnecting (default: 90)
#
# Architecture:
#   Cloud Run → VM:LISTEN_PORT → socat → TARGET_HOST:TARGET_PORT (via VPN)
#
# The main loop monitors ppp0 and socat; if either dies it reconnects the VPN
# internally (no Docker container restarts needed).
# ==============================================================================
set -uo pipefail

VPN_SERVER="${VPN_SERVER:?VPN_SERVER required}"
VPN_PSK="${VPN_PSK:?VPN_PSK required}"
TARGET_HOST="${TARGET_HOST:-10.0.0.22}"
TARGET_PORT="${TARGET_PORT:-1433}"
VPN_USER="${VPN_USER:?VPN_USER required}"
VPN_PASS="${VPN_PASS:?VPN_PASS required}"
LISTEN_PORT="${LISTEN_PORT:-1433}"
RECONNECT_DELAY="${RECONNECT_DELAY:-90}"

echo "[vpn] Starting persistent relay: $VPN_SERVER -> $TARGET_HOST:$TARGET_PORT (listen :$LISTEN_PORT)"

# ── Kernel modules ────────────────────────────────────────────────────────────
modprobe ppp_generic 2>/dev/null || true
modprobe l2tp_ppp    2>/dev/null || true
[ -e /dev/ppp ] || mknod /dev/ppp c 108 0
chmod 666 /dev/ppp

# ── IPSec config (IKEv1, transport mode for L2TP) ────────────────────────────
cat > /etc/ipsec.conf <<IPSEC
config setup
  uniqueids=never
  charondebug="ike 1, knl 0, cfg 0, net 1"
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
  ike=aes256-sha256-modp2048,aes256-sha1-modp2048,aes128-sha256-modp2048,aes128-sha1-modp2048,3des-sha1-modp1024!
  esp=aes256-sha256,aes256-sha1,aes128-sha256,aes128-sha1,3des-sha1!
  ikelifetime=86400s
  keylife=3600s
  dpdaction=restart
  dpddelay=30s
  closeaction=clear
IPSEC
printf "%%any %s : PSK \"%s\"\n" "$VPN_SERVER" "$VPN_PSK" > /etc/ipsec.secrets
chmod 600 /etc/ipsec.secrets

# ── xl2tpd + ppp config ───────────────────────────────────────────────────────
mkdir -p /etc/xl2tpd /etc/ppp /var/run/xl2tpd /var/log

cat > /etc/xl2tpd/xl2tpd.conf <<XL
[global]
port = 1701
[lac youngsinc]
lns = ${VPN_SERVER}
ppp debug = yes
pppoptfile = /etc/ppp/options.youngsinc
length bit = yes
XL

cat > /etc/ppp/options.youngsinc <<PPP
ipcp-accept-local
ipcp-accept-remote
refuse-eap
noccp
noauth
mtu 1280
mru 1280
noipdefault
defaultroute
connect-delay 5000
name ${VPN_USER}
password ${VPN_PASS}
debug
logfile /var/log/pppd.log
PPP
chmod 600 /etc/ppp/options.youngsinc

printf "\"%s\" * \"%s\" *\n" "$VPN_USER" "$VPN_PASS" > /etc/ppp/chap-secrets
chmod 600 /etc/ppp/chap-secrets

# ── Helper functions ──────────────────────────────────────────────────────────

vpn_teardown() {
  echo "[vpn] Tearing down session..."
  [ -n "${SOCAT_PID:-}" ] && kill "$SOCAT_PID" 2>/dev/null || true
  SOCAT_PID=""
  if [ -f /var/run/xl2tpd.pid ]; then
    kill "$(cat /var/run/xl2tpd.pid)" 2>/dev/null || true
    rm -f /var/run/xl2tpd.pid
  fi
  killall xl2tpd 2>/dev/null || true
  killall pppd   2>/dev/null || true
  rm -f /var/run/xl2tpd/l2tp-control
  ipsec down youngsinc 2>/dev/null || true
  sleep 2
}

vpn_ipsec_up() {
  if ! ipsec statusall 2>/dev/null | grep -q "Security Associations"; then
    echo "[vpn] Starting IPSec daemon..."
    ipsec start 2>/dev/null || true
    sleep 5
  fi
  ipsec up youngsinc 2>/dev/null || true
  local I=0 MAX=30
  while ! ipsec statusall 2>/dev/null | grep -q "ESTABLISHED"; do
    sleep 1; I=$((I+1))
    [ $I -ge $MAX ] && { echo "[vpn] IPSec TIMEOUT"; ipsec statusall; return 1; }
    echo "[vpn] IPSec wait $I/$MAX"
  done
  echo "[vpn] IPSec ESTABLISHED"
  return 0
}

vpn_l2tp_up() {
  touch /var/run/pluto.ctl
  xl2tpd -D &
  sleep 5
  echo "c youngsinc" > /var/run/xl2tpd/l2tp-control 2>/dev/null || true
  sleep 5

  local I=0 MAX=45
  while ! ip link show ppp0 &>/dev/null; do
    sleep 1; I=$((I+1))
    [ $I -ge $MAX ] && { echo "[vpn] ppp0 link timeout"; return 1; }
    echo "[vpn] ppp0 link wait $I/$MAX"
  done

  I=0; MAX=45
  while ! ip addr show ppp0 2>/dev/null | grep -q "inet "; do
    sleep 1; I=$((I+1))
    [ $I -ge $MAX ] && {
      echo "[vpn] ppp0 IP timeout; pppd log:"
      tail -30 /var/log/pppd.log 2>/dev/null || true
      return 1
    }
    echo "[vpn] ppp0 IP wait $I/$MAX"
  done

  ip route add 10.0.0.0/8 dev ppp0 2>/dev/null || true
  echo "[vpn] ppp0 UP: $(ip addr show ppp0 | grep inet)"
  return 0
}

vpn_connect() {
  # Up to 5 attempts, with RECONNECT_DELAY between each (server needs time to clear session)
  local ATTEMPT=0 MAX_ATTEMPTS=5
  while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    ATTEMPT=$((ATTEMPT+1))
    echo "[vpn] Connect attempt $ATTEMPT/$MAX_ATTEMPTS"
    vpn_teardown
    if [ $ATTEMPT -gt 1 ]; then
      echo "[vpn] Waiting ${RECONNECT_DELAY}s for server to clear session..."
      sleep "$RECONNECT_DELAY"
    fi
    vpn_ipsec_up || continue
    vpn_l2tp_up  && return 0
    echo "[vpn] L2TP failed on attempt $ATTEMPT"
  done
  echo "[vpn] All $MAX_ATTEMPTS attempts exhausted"
  return 1
}

# ── Start IPSec daemon once ───────────────────────────────────────────────────
ipsec start 2>/dev/null || true
sleep 3

# ── Main supervisor loop ──────────────────────────────────────────────────────
# Keeps the relay running forever. If ppp0 or socat dies, reconnects
# automatically without restarting the Docker container.
SOCAT_PID=""
FIRST_RUN=1

while true; do
  echo "[vpn] === New connection cycle ==="

  if [ $FIRST_RUN -eq 1 ]; then
    FIRST_RUN=0
    vpn_ipsec_up || { vpn_connect || { echo "[vpn] Cannot establish VPN. Retrying in 120s"; sleep 120; continue; }; }
    vpn_l2tp_up  || { vpn_connect || { echo "[vpn] Cannot establish VPN. Retrying in 120s"; sleep 120; continue; }; }
  else
    vpn_connect || { echo "[vpn] Cannot establish VPN. Retrying in 120s"; sleep 120; continue; }
  fi

  if timeout 10 bash -c "echo >/dev/tcp/$TARGET_HOST/$TARGET_PORT" 2>/dev/null; then
    echo "[vpn] SUCCESS: $TARGET_HOST:$TARGET_PORT reachable via VPN"
  else
    echo "[vpn] WARNING: $TARGET_HOST:$TARGET_PORT not yet reachable, starting socat anyway"
  fi

  echo "[vpn] socat TCP-LISTEN:$LISTEN_PORT -> $TARGET_HOST:$TARGET_PORT"
  socat TCP-LISTEN:$LISTEN_PORT,fork,reuseaddr TCP:$TARGET_HOST:$TARGET_PORT &
  SOCAT_PID=$!
  echo "[vpn] socat PID=$SOCAT_PID"

  while true; do
    sleep 15
    if ! kill -0 "$SOCAT_PID" 2>/dev/null; then
      echo "[vpn] socat exited — will reconnect"
      break
    fi
    if ! ip link show ppp0 &>/dev/null; then
      echo "[vpn] ppp0 disappeared — will reconnect"
      kill "$SOCAT_PID" 2>/dev/null || true
      SOCAT_PID=""
      break
    fi
  done

  echo "[vpn] Session lost. Reconnecting after ${RECONNECT_DELAY}s..."
  sleep "$RECONNECT_DELAY"
done
