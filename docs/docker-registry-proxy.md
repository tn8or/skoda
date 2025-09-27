# Docker Registry Proxy Configuration

This document describes the Docker registry proxy configuration implemented for the Skoda Data Logger project.

## Overview

The project uses a Docker registry proxy to:
- Speed up Docker image pulls by caching frequently used images locally
- Reduce external dependencies and network traffic
- Improve build reliability in environments with limited internet connectivity
- Support air-gapped or restricted network environments

## Docker Hub Authentication

To avoid Docker Hub rate limits, the workflows support Docker Hub authentication via repository secrets:

- `DOCKERHUB_USERNAME`: Your Docker Hub username
- `DOCKERHUB_TOKEN`: Your Docker Hub access token or password

These secrets are optional but recommended to avoid rate limiting issues. When configured, the workflows will:
1. Log in to Docker Hub before starting any Docker operations
2. Configure Docker daemon authentication for subsequent operations
3. Use the registry proxy while maintaining authentication

**Important**: The Docker Hub authentication now occurs at the workflow level before any Docker images are pulled, which prevents rate limiting issues during the initial container setup.

### Setting up Docker Hub Secrets

1. Create a Docker Hub access token at: https://hub.docker.com/settings/security
2. Add the following repository secrets in GitHub:
   - `DOCKERHUB_USERNAME`: Your Docker Hub username
   - `DOCKERHUB_TOKEN`: The access token you created

## Configuration

### Registry Proxy Settings

- **Proxy URL**: `http://dockerproxy.lan:80`
- **Insecure Registry**: `dockerproxy.lan:80`

The configuration is stored in `.github/docker-daemon.json`:

```json
{
  "registry-mirrors": [
    "http://dockerproxy.lan:80"
  ],
  "insecure-registries": [
    "dockerproxy.lan:80"
  ]
}
```

## Automatic Configuration

### GitHub Actions Workflows

The Docker registry proxy is automatically configured in all GitHub Actions workflows:

1. **CI Workflow** (`.github/workflows/ci.yml`) - Uses `python:3.13-slim` container
2. **Docker Build Workflow** (`.github/workflows/ghcr-image.yml`) - Runs on self-hosted runner with Docker daemon configuration
3. **Test Image Workflow** (`.github/workflows/test-image.yml`) - Runs on self-hosted runner with Docker daemon configuration
4. **Pip Audit Workflow** (`.github/workflows/pip-audit.yml`) - Uses `python:3.13-slim` container
5. **Update Dependencies Workflow** (`.github/workflows/update-deps.yml`) - Uses `python:3.13-slim` container

**Note**: The Docker Build and Test Image workflows have been modified to run directly on self-hosted runners instead of using `docker:27-dind` containers. This approach prevents Docker Hub rate limiting issues during the initial container image pull by authenticating with Docker Hub before any Docker operations begin.

Each Docker workflow includes steps that:
1. Authenticate with Docker Hub before any Docker operations
2. Create the Docker daemon configuration directory
3. Copy the registry proxy configuration
4. Restart the Docker daemon to apply configurations
5. Wait for services to be ready

### Container Specifications for Kubernetes Mode

All workflows support Kubernetes mode self-hosted runners with appropriate container specifications:

- **Python workflows**: Use `python:3.13-slim` container
- **Docker workflows**: Run directly on self-hosted runners without container specification to avoid Docker Hub rate limits
- **Webhook workflows**: Use `alpine:3.19` container

**Note**: The Docker Build and Test Image workflows have been modified to run directly on self-hosted runners instead of using container specifications. This prevents Docker Hub rate limiting issues when GitHub Actions tries to pull the job container image.

### Example Workflow Step (Docker on Self-hosted Runner)

```yaml
jobs:
  build-and-push-image:
    runs-on: skoda-runner-set
    timeout-minutes: 90
    steps:
      - name: Checkout repository
        uses: actions/checkout@v5

      - name: Log in to Docker Hub (before Docker operations)
        continue-on-error: true
        uses: docker/login-action@v3
        with:
          username: ${{ secrets.DOCKERHUB_USERNAME }}
          password: ${{ secrets.DOCKERHUB_TOKEN }}

      - name: Set up Docker with registry proxy
        run: |
          # Configure Docker daemon with registry proxy
          sudo mkdir -p /etc/docker
          sudo cp .github/docker-daemon.json /etc/docker/daemon.json
          
          # Add Docker Hub authentication if credentials are available
          if [ -n "${{ secrets.DOCKERHUB_USERNAME }}" ] && [ -n "${{ secrets.DOCKERHUB_TOKEN }}" ]; then
            echo "Configuring Docker Hub authentication..."
            mkdir -p ~/.docker
            echo '{"auths":{"https://index.docker.io/v1/":{"username":"${{ secrets.DOCKERHUB_USERNAME }}","password":"${{ secrets.DOCKERHUB_TOKEN }}"}}}' > ~/.docker/config.json
          fi
          
          # Restart Docker daemon to apply registry proxy configuration
          sudo systemctl restart docker
          
          # Wait for Docker daemon to be ready
          sleep 10
          while ! docker info > /dev/null 2>&1; do
            echo "Waiting for Docker daemon to start..."
            sleep 5
          done
          
          # Verify Docker is running and show registry mirrors
          docker info
```

### Example Workflow Step (Python-only)

```yaml
jobs:
  test:
    runs-on: skoda-runner-set
    container:
      image: python:3.13-slim
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

4. **Docker Hub Rate Limit Issues**
   - Set up Docker Hub authentication via repository secrets: `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
   - The workflows now authenticate with Docker Hub before any Docker operations to prevent rate limiting
   - If rate limits persist, verify the Docker Hub credentials are correct
   - Consider using a Docker registry proxy for additional caching

5. **Kubernetes Mode Issues**
   - Container options like `--user root` can cause permission issues in Kubernetes
   - Use minimal container options for better compatibility
   - Check that the container image is accessible from the Kubernetes cluster

6. **Self-hosted Runner Docker Issues**
   - Ensure Docker is installed and running on the self-hosted runner
   - Verify the runner user has `sudo` privileges for Docker daemon configuration
   - Check that the Docker daemon can be restarted via `systemctl`

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