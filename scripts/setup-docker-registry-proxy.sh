#!/bin/bash
# Setup Docker registry proxy for local development
# This script configures Docker daemon to use the local registry proxy

set -e

DOCKER_DAEMON_DIR="/etc/docker"
DOCKER_DAEMON_JSON="$DOCKER_DAEMON_DIR/daemon.json"
REGISTRY_PROXY="dockerproxy.lan:80"
LOCAL_REGISTRY="local-registry.default.svc.cluster.local:5000"

echo "Setting up Docker registry proxy configuration..."

# Check if running with sufficient privileges
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with sudo privileges to configure Docker daemon"
    echo "Usage: sudo ./scripts/setup-docker-registry-proxy.sh"
    exit 1
fi

# Create Docker daemon directory if it doesn't exist
mkdir -p "$DOCKER_DAEMON_DIR"

# Backup existing daemon.json if it exists
if [ -f "$DOCKER_DAEMON_JSON" ]; then
    echo "Backing up existing daemon.json to daemon.json.backup"
    cp "$DOCKER_DAEMON_JSON" "$DOCKER_DAEMON_JSON.backup"
fi

# Create the daemon.json with registry proxy configuration
echo "Configuring Docker daemon with registry proxy: $REGISTRY_PROXY"
echo "Configuring Docker daemon with local registry cache: $LOCAL_REGISTRY"
cat > "$DOCKER_DAEMON_JSON" << EOF
{
  "registry-mirrors": [
    "http://$REGISTRY_PROXY"
  ],
  "insecure-registries": [
    "$REGISTRY_PROXY",
    "$LOCAL_REGISTRY"
  ]
}
EOF

echo "Docker daemon configuration written to $DOCKER_DAEMON_JSON"

# Restart Docker daemon
echo "Restarting Docker daemon to apply configuration..."
systemctl restart docker

# Wait for Docker daemon to be ready
echo "Waiting for Docker daemon to be ready..."
sleep 5

# Verify Docker is running
if systemctl is-active --quiet docker; then
    echo "✅ Docker daemon is running with registry proxy configuration"
    echo "Registry mirror: http://$REGISTRY_PROXY"
    echo "Insecure registries: $REGISTRY_PROXY, $LOCAL_REGISTRY"
else
    echo "❌ Docker daemon failed to start. Check the configuration."
    exit 1
fi

echo "Docker registry proxy setup completed successfully!"