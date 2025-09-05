# Security

## GitHub Actions Security

This repository follows GitHub Actions security best practices to prevent execution of untrusted code in privileged contexts.

### Workflow Security Model

- **CI Workflow (`ci.yml`)**: Runs on `pull_request` and `push` triggers in unprivileged context
- **Docker Build Workflow (`ghcr-image.yml`)**: Uses `workflow_run` trigger for privileged operations
- **Dependency Updates (`update-deps.yml`)**: Uses `schedule` and `workflow_dispatch` triggers only
- **Security Audits (`pip-audit.yml`)**: Runs on `pull_request` and `push` in unprivileged context

### Security Measures Implemented

1. **Isolation of Untrusted Code**: Pull requests run in unprivileged `pull_request` context without access to secrets
2. **Privileged Operations**: Docker image building and deployment use `workflow_run` trigger that only executes after successful CI on trusted branches
3. **Trusted Code Checkout**: The `ghcr-image.yml` workflow explicitly checks out the `main` branch instead of potentially untrusted PR code
4. **Branch Protection**: The Docker workflow only runs on pushes to the `main` branch

### Avoided Anti-Patterns

- ❌ `pull_request_target` trigger (not used)
- ❌ `issue_comment` trigger (not used)
- ❌ Checking out untrusted code in privileged contexts
- ❌ Exposing secrets to untrusted PRs

### Security Controls

The `ghcr-image.yml` workflow includes these security controls:

```yaml
# Only run on successful CI from push events to main branch
if: ${{ github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push' && github.event.workflow_run.head_branch == 'main' }}

# Always checkout trusted main branch, never untrusted PR code
ref: main
```

This ensures that Docker images are only built from trusted code that has passed CI and been merged to the main branch.