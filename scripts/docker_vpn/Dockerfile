# ==============================================================================
# Youngsinc IKEv2/IPSec VPN tunnel container  (upgraded from IKEv1/L2TP)
# Connects to remote.youngsinc.com and forwards
# 10.0.0.22:1433 (SQL Server) to container port 1433.
# Mac sees it as localhost:14333 via docker -p 14333:1433.
#
# IKEv2, AES-256, SHA-512, DH Group 20 (ecp384)
# No xl2tpd / PPP / OpenSSL legacy provider needed.
# ==============================================================================
FROM debian:bookworm-slim

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        strongswan \
        libstrongswan-standard-plugins \
        libstrongswan-extra-plugins \
        socat \
        iproute2 \
        iputils-ping \
        procps \
        net-tools \
        curl \
        openssl \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 1433
EXPOSE 500/udp
EXPOSE 4500/udp

ENTRYPOINT ["/entrypoint.sh"]
