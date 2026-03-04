# ==============================================================================
# Youngsinc L2TP/IPSec VPN tunnel container
# Connects to remote.youngsinc.com via IKEv1 transport + L2TP/PPP and
# forwards 0.0.0.0:1433 → 10.0.0.22:1433 (YISBeta SQL Server).
#
# Required host capabilities: --privileged --cap-add NET_ADMIN SYS_MODULE
#                              --device /dev/ppp:/dev/ppp
# Required kernel modules on host: ppp_generic, l2tp_ppp
# ==============================================================================
FROM debian:bookworm-slim

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        strongswan \
        libstrongswan-standard-plugins \
        libstrongswan-extra-plugins \
        xl2tpd \
        ppp \
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
