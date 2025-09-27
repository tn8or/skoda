#!/bin/sh
set -e

# Function to check if Docker registry proxy should be configured
configure_docker_proxy() {
    if [ -f ".github/docker-daemon.json" ] && [ "${DOCKER_REGISTRY_PROXY:-}" = "true" ]; then
        echo "Configuring Docker daemon with registry proxy..."
        if [ "$EUID" -eq 0 ] || sudo -n true 2>/dev/null; then
            sudo mkdir -p /etc/docker
            sudo cp .github/docker-daemon.json /etc/docker/daemon.json
            sudo systemctl restart docker
            sleep 5
            echo "Docker registry proxy configured successfully"
        else
            echo "Warning: Cannot configure Docker registry proxy without sudo access"
            echo "Run with DOCKER_REGISTRY_PROXY=true and sudo privileges, or use:"
            echo "  sudo ./scripts/setup-docker-registry-proxy.sh"
        fi
    fi
}

if [ $1 = "up" ]; then
# Configure Docker registry proxy if requested
configure_docker_proxy

source .venv/bin/activate
pytest -q
folders="skodaimporter skodachargefinder skodachargecollector skodaupdatechargeprices skodachargefrontend"
echo ${folders} | xargs -P 8 -t -n 1 -I {} sh -c 'pip-compile --upgrade --output-file={}/requirements.txt {}/requirements.in'
echo compiled requirements
GIT_COMMIT=$(git rev-parse HEAD || true)
GIT_TAG=$(git describe --tags --always --dirty || true)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
docker compose build \
	--build-arg GIT_COMMIT="${GIT_COMMIT}" \
	--build-arg GIT_TAG="${GIT_TAG}" \
	--build-arg BUILD_DATE="${BUILD_DATE}"
fi
docker compose $1
