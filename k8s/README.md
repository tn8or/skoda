# Kubernetes Manifests for Skoda Platform

This directory contains Kubernetes manifests for deploying the Skoda Data Logger platform to Kubernetes.

## Structure

```
k8s/
├── base/                          # Base Kustomize configuration (all manifests)
│   ├── namespace.yaml
│   ├── rbac.yaml
│   ├── configmap-apps.yaml        # Non-sensitive configuration
│   ├── service-importer.yaml      # All services
│   ├── deployment-importer.yaml   # All deployments
│   ├── deployment-*.yaml
│   └── kustomization.yaml
│
├── overlays/                       # Environment-specific overrides
│   ├── prod/
│   │   ├── kustomization.yaml     # Production settings (3 replicas, etc)
│   │   └── configmap-prod.yaml
│   └── dev/
│       ├── kustomization.yaml     # Dev settings (1 replica, low resources)
│       └── configmap-dev.yaml
│
├── ArgoCD-Application.yaml        # ArgoCD Application manifest
├── SECRETS_CONTRACT.md            # Required secrets from Ansible vault
└── README.md                       # This file
```

## Deployment

### Prerequisites

- Kubernetes 1.24+
- Kustomize (included in `kubectl`)
- Container images in registry: `ghcr.io/tn8or/skoda/*`
- Secrets provisioned by Ansible/vault (see SECRETS_CONTRACT.md)

### Via ArgoCD (Recommended)

1. Create ArgoCD namespace and application:

```bash
kubectl create namespace argocd
kubectl apply -f k8s/ArgoCD-Application.yaml
```

1. Monitor sync:

```bash
argocd app get skoda
argocd app sync skoda
```

### Manual via Kustomize

Deploy base manifests:

```bash
kubectl apply -k k8s/base
```

Deploy production environment:

```bash
kubectl apply -k k8s/overlays/prod
```

Deploy development environment:

```bash
kubectl apply -k k8s/overlays/dev
```

## Secrets

**IMPORTANT**: Kubernetes Secrets must be provisioned BEFORE deploying manifests.

See [SECRETS_CONTRACT.md](./SECRETS_CONTRACT.md) for:

- Required secret names and keys
- Mapping to Ansible vault variables
- Validation procedures

Example provisioning (for development only):

```bash
kubectl create secret generic skoda-credentials \
  --from-literal=SKODA_USER='testuser' \
  --from-literal=SKODA_PASS='testpass' \
  -n skoda

kubectl create secret generic mariadb-credentials \
  --from-literal=MARIADB_USERNAME='skoda' \
  --from-literal=MARIADB_PASSWORD='skodapass' \
  -n skoda

kubectl create secret generic graylog-credentials \
  --from-literal=GRAYLOG_HOST='graylog.monitoring.svc.cluster.local' \
  -n skoda
```

## Services

All services are exposed as ClusterIP Services within the `skoda` namespace:

- `skodaimporter:80` - MySkoda API data importer
- `skodachargefinder:80` - Charge event detection
- `skodachargecollector:80` - Charge data aggregation
- `skodaupdatechargeprices:80` - Price updates
- `skodachargefrontend:80` - Web UI

To expose externally, use Ingress (configure in central ArgoCD repository).

## Health Checks

Each deployment includes:

- **Liveness probe**: Checks service health every 30s (restart after 3 failures)
- **Readiness probe**: Checks service readiness every 10s (remove from load balancing after 2 failures)

All probes hit the root endpoint `/` which returns `{"status": "healthy"}` or similar.

## Resource Requests/Limits

**Base configuration** (development):

- Request: 256Mi memory, 100m CPU
- Limit: 512Mi memory, 500m CPU

**Production override** (optional in overlay):

- Increase replicas to 3 for stateless services
- Increase resource limits as needed

## Database

MariaDB connectivity configured via `mariadb-app-config`:

- Host: `mariadb.skoda.svc.cluster.local`
- Database: `skoda`
- Credentials: from `mariadb-credentials` secret

Database must be initialized with schema from `../sqldump/sqldump.sql` before first application startup.

## Logging

All services send logs to Graylog via UDP:

- Host: injected from `graylog-credentials` secret
- Port: 12201 (static, from `graylog-app-config`)

## Image Registry

All images pull from:

- Registry: `ghcr.io`
- Owner: `tn8or`
- Image names: `skoda/skodaimporter`, `skoda/skodachargefinder`, etc.
- ImagePullPolicy: `IfNotPresent`

Ensure images are pre-built and pushed before deploying. Container images should be tagged with Git commit SHA for reproducibility:

```bash
COMMIT_SHA=$(git rev-parse --short HEAD)
docker build -t ghcr.io/tn8or/skoda/skodaimporter:${COMMIT_SHA} ./skodaimporter
docker push ghcr.io/tn8or/skoda/skodaimporter:${COMMIT_SHA}
docker tag ghcr.io/tn8or/skoda/skodaimporter:${COMMIT_SHA} ghcr.io/tn8or/skoda/skodaimporter:latest
docker push ghcr.io/tn8or/skoda/skodaimporter:latest
```

## Customization

To customize for your environment:

1. **Change replicas**: Edit `overlays/prod/kustomization.yaml`
2. **Change resources**: Create patch file in overlay directory
3. **Change environment variables**: Edit `overlays/*/configmap-*.yaml`
4. **Change images**: Edit `overlays/*/kustomization.yaml` with `images:` section
5. **Add Ingress**: Create `overlays/*/ingress.yaml`

Example adding Ingress to overlay:

```yaml
# overlays/prod/ingress.yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: skoda-frontend
  namespace: skoda
spec:
  ingressClassName: nginx
  rules:
  - host: skoda.example.com
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: skodachargefrontend
            port:
              number: 80
```

Then add to `overlays/prod/kustomization.yaml`:

```yaml
resources:
- ../../base
- ingress.yaml
```

## Troubleshooting

### Pods not starting

Check secret provisioning:

```bash
kubectl describe pod <pod-name> -n skoda
kubectl get secrets -n skoda
```

### Service connectivity issues

Test DNS resolution:

```bash
kubectl exec -it <pod-name> -n skoda -- nslookup mariadb.skoda.svc.cluster.local
```

### Viewing logs

```bash
kubectl logs -n skoda -l app.kubernetes.io/name=skoda -f
```

### Restarting deployments

```bash
kubectl rollout restart deployment -n skoda
```

## GitOps Workflow

This repository (<https://github.com/tn8or/skoda>) contains the application code AND Kubernetes manifests.

ArgoCD monitors this repository's `k8s/` directory and automatically:

1. Pulls manifests from Git
2. Applies Kustomize overlays based on environment
3. Injects secrets from Kubernetes (created by Ansible in central repo)
4. Reconciles cluster state to match Git state

To deploy a change:

```bash
git commit -am "Update resource limits for production"
git push origin main
# ArgoCD will automatically detect and deploy changes
argocd app sync skoda  # or wait for auto-sync
```
