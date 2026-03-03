#!/bin/bash
# Stop and remove the Youngsinc VPN tunnel container
CONTAINER_NAME="youngsinc-tunnel"

if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo "==> Stopping ${CONTAINER_NAME}..."
    docker rm -f "${CONTAINER_NAME}"
    echo "Tunnel stopped."
else
    echo "No running tunnel container found."
fi
