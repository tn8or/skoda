#!/bin/bash
# Restore original Docker daemon configuration
# This script removes the registry proxy configuration and restores the backup

set -e

DOCKER_DAEMON_DIR="/etc/docker"
DOCKER_DAEMON_JSON="$DOCKER_DAEMON_DIR/daemon.json"
BACKUP_FILE="$DOCKER_DAEMON_JSON.backup"

echo "Restoring original Docker configuration..."

# Check if running with sufficient privileges
if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run with sudo privileges to configure Docker daemon"
    echo "Usage: sudo ./scripts/restore-docker-config.sh"
    exit 1
fi

# Check if backup exists
if [ -f "$BACKUP_FILE" ]; then
    echo "Restoring from backup: $BACKUP_FILE"
    cp "$BACKUP_FILE" "$DOCKER_DAEMON_JSON"
    echo "✅ Original configuration restored"
elif [ -f "$DOCKER_DAEMON_JSON" ]; then
    echo "Removing registry proxy configuration (no backup found)"
    rm "$DOCKER_DAEMON_JSON"
    echo "✅ Registry proxy configuration removed"
else
    echo "No Docker daemon configuration found to restore"
fi

# Restart Docker daemon
echo "Restarting Docker daemon..."
systemctl restart docker

# Wait for Docker daemon to be ready
echo "Waiting for Docker daemon to be ready..."
sleep 5

# Verify Docker is running
if systemctl is-active --quiet docker; then
    echo "✅ Docker daemon is running with restored configuration"
else
    echo "❌ Docker daemon failed to start. Check the configuration."
    exit 1
fi

echo "Docker configuration restoration completed successfully!"