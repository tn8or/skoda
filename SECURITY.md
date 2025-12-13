# Security

## GitHub Actions Security

This repository follows GitHub Actions security best practices to prevent execution of untrusted code in privileged contexts.

### Workflow Security Model

- **CI/CD Pipeline (`ci-cd.yml`)**: Combined workflow that handles testing, security audits, and image building
  - Runs on `pull_request` and `push` triggers for testing and security audits
  - Builds test images for PRs and non-main branches
  - Builds production images only after successful tests on main branch
  - Includes pip-audit security scanning for all services
- **Dependency Updates (`update-deps.yml`)**: Uses `schedule` and `workflow_dispatch` triggers only

### Security Measures Implemented

1. **Isolation of Untrusted Code**: Pull requests run in unprivileged `pull_request` context without access to deployment secrets
2. **Conditional Image Building**: Production images with deployment webhooks only execute after successful tests on main branch
3. **Test vs Production Separation**: PR builds create test images only, without triggering production deployments
4. **Branch Protection**: Production image builds and deployment webhooks only run on pushes to the `main` branch
5. **Security Scanning**: pip-audit runs on all pull requests and pushes to detect dependency vulnerabilities

### Avoided Anti-Patterns

- ❌ `pull_request_target` trigger (not used)
- ❌ `issue_comment` trigger (not used)
- ❌ Checking out untrusted code in privileged contexts
- ❌ Exposing secrets to untrusted PRs

### Security Controls

The `ci-cd.yml` workflow includes these security controls:

```yaml
# Test images built for non-main branches (PRs)
build-test-images:
  if: github.ref != 'refs/heads/main'
  needs: [test]
  
# Production images only built from main branch
build-production-images:
  if: github.ref == 'refs/heads/main'
  needs: [test]
  
# Production webhooks only triggered after successful production builds
notify-production-webhook:
  if: github.ref == 'refs/heads/main'
  needs: [build-production-images]
```

This ensures that:
- All code must pass tests before any images are built
- Production images and deployments only occur from trusted main branch code
- PRs can build test images but cannot trigger production deployments