# Docker Registry Proxy Configuration

This document describes the Docker registry proxy configuration implemented for the Skoda Data Logger project.

## Overview

The project uses a Docker registry proxy to:
- Speed up Docker image pulls by caching frequently used images locally
- Reduce external dependencies and network traffic
- Improve build reliability in environments with limited internet connectivity
- Support air-gapped or restricted network environments

## Configuration

### Registry Proxy Settings

- **Proxy URL**: `http://docker-registry-proxy.docker-registry-proxy.svc.cluster.local:3128`
- **Insecure Registry**: `docker-registry-proxy.docker-registry-proxy.svc.cluster.local:3128`

The configuration is stored in `.github/docker-daemon.json`:

```json
{
  "registry-mirrors": [
    "http://docker-registry-proxy.docker-registry-proxy.svc.cluster.local:3128"
  ],
  "insecure-registries": [
    "docker-registry-proxy.docker-registry-proxy.svc.cluster.local:3128"
  ]
}
```

## Automatic Configuration

### GitHub Actions Workflows

The Docker registry proxy is automatically configured in all GitHub Actions workflows:

1. **CI Workflow** (`.github/workflows/ci.yml`) - Uses `python:3.13-slim` container
2. **Docker Build Workflow** (`.github/workflows/ghcr-image.yml`) - Uses `docker:27-dind` container with Docker-in-Docker
3. **Test Image Workflow** (`.github/workflows/test-image.yml`) - Uses `docker:27-dind` container with Docker-in-Docker
4. **Pip Audit Workflow** (`.github/workflows/pip-audit.yml`) - Uses `python:3.13-slim` container
5. **Update Dependencies Workflow** (`.github/workflows/update-deps.yml`) - Uses `python:3.13-slim` container

All workflows are configured with `container:` specifications to support Kubernetes mode self-hosted runners.

Each workflow includes a step that:
1. Creates the Docker daemon configuration directory
2. Copies the registry proxy configuration
3. For Docker-in-Docker workflows: Starts dockerd with the configuration
4. For Python-only workflows: Uses pre-configured container images
5. Waits for services to be ready

### Container Specifications for Kubernetes Mode

All workflows support Kubernetes mode self-hosted runners with appropriate container specifications:

- **Python workflows**: Use `python:3.13-slim` with `--user root` options
- **Docker workflows**: Use `docker:27-dind` with `--privileged --user root` options  
- **Webhook workflows**: Use `alpine:3.19` with `--user root` options

### Example Workflow Step (Docker-in-Docker)

```yaml
jobs:
  build-and-push-image:
    runs-on: skoda-runner-set
    container:
      image: docker:27-dind
      options: --privileged --user root
    steps:
      - name: Configure Docker daemon with registry proxy
        run: |
          # For Docker-in-Docker, configure daemon directly
          mkdir -p /etc/docker
          
          # Copy Docker daemon configuration with registry proxy settings
          cp .github/docker-daemon.json /etc/docker/daemon.json
          
          # Start Docker daemon in background for DinD
          dockerd --host=unix:///var/run/docker.sock --host=tcp://0.0.0.0:2375 &
          
          # Wait for Docker daemon to be ready
          sleep 10
```

### Example Workflow Step (Python-only)

```yaml
jobs:
  test:
    runs-on: skoda-runner-set
    container:
      image: python:3.13-slim
      options: --user root
    steps:
      # Docker registry proxy not needed for Python-only workflows
      - name: Checkout
        uses: actions/checkout@v5
```

## Local Development

### Manual Setup

Use the provided scripts to configure your local Docker daemon:

```bash
# Setup Docker registry proxy
sudo ./scripts/setup-docker-registry-proxy.sh

# Restore original configuration
sudo ./scripts/restore-docker-config.sh
```

### Automated Setup with compose.sh

Enable registry proxy configuration when using the build script:

```bash
# Enable Docker registry proxy during build
DOCKER_REGISTRY_PROXY=true sudo ./compose.sh up
```

## Testing

Validate the Docker registry proxy configuration:

```bash
# Test configuration validity
python scripts/test-docker-config.py

# Check Docker daemon status
sudo systemctl status docker

# Verify Docker info shows registry mirrors
docker info | grep -A 10 "Registry Mirrors"
```

## Troubleshooting

### Common Issues

1. **Permission Denied**
   - Ensure you run scripts with `sudo` privileges
   - Check that the user has permission to restart Docker daemon

2. **Docker Daemon Won't Start**
   - Validate JSON configuration: `python -c "import json; json.load(open('.github/docker-daemon.json'))"`
   - Check Docker daemon logs: `sudo journalctl -u docker.service`

3. **Registry Proxy Not Working**
   - Verify network connectivity to the proxy URL
   - Check if the proxy service is running in the cluster
   - Confirm insecure registry configuration is correct

### Restoration

If issues occur, restore the original Docker configuration:

```bash
sudo ./scripts/restore-docker-config.sh
```

This will:
- Restore the backup configuration (if available)
- Remove registry proxy settings (if no backup exists)
- Restart the Docker daemon

## Security Considerations

- The registry proxy is configured as an **insecure registry** to allow HTTP connections
- This is acceptable for internal/private networks but should not be used with public registries
- Always verify the proxy server's security and access controls
- Consider using HTTPS with proper certificates for production environments

## Files

- `.github/docker-daemon.json` - Docker daemon configuration with registry proxy settings
- `scripts/setup-docker-registry-proxy.sh` - Setup script for local development
- `scripts/restore-docker-config.sh` - Restoration script to remove proxy configuration
- `scripts/test-docker-config.py` - Validation script for configuration testing
- `compose.sh` - Updated build script with optional proxy configuration