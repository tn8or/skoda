# Kubernetes Secrets Contract for Skoda Platform

This document defines the exact secrets that must be provisioned in the Kubernetes cluster for the Skoda platform to function. These secrets are created by Ansible vault variables and managed by the central ArgoCD/Ansible repository.

## Required Kubernetes Secrets

### 1. `skoda-credentials` (Type: Opaque)

**Namespace**: `skoda`

**Purpose**: MySkoda API and optional vehicle-specific configuration

**Required Keys**:

```yaml
SKODA_USER: <string>       # MySkoda API username
SKODA_PASS: <string>       # MySkoda API password
env: <string>              # Runtime environment (e.g. "prod", "dev")
```

**Optional Keys**:

```yaml
SKODA_AUTH: <string>       # Custom auth token (if applicable)
SKODA_EVENTS: <string>     # Event filtering config (JSON or comma-separated)
SKODA_VEHICLE: <string>    # Vehicle VIN or identifier
```

**Ansible Vault Variables** (from central repo):

- `skoda_user` → SKODA_USER
- `skoda_pass` → SKODA_PASS
- `env` → env
- `skoda_auth` → SKODA_AUTH (optional)
- `skoda_events` → SKODA_EVENTS (optional)
- `skoda_vehicle` → SKODA_VEHICLE (optional)

**Example creation command**:

```bash
kubectl create secret generic skoda-credentials \
  --from-literal=SKODA_USER='<user>' \
  --from-literal=SKODA_PASS='<pass>' \
  --from-literal=env='prod' \
  --from-literal=SKODA_AUTH='<auth>' \
  --from-literal=SKODA_EVENTS='<events>' \
  --from-literal=SKODA_VEHICLE='<vehicle>' \
  -n skoda
```

---

### 2. `mariadb-credentials` (Type: Opaque)

**Namespace**: `skoda`

**Purpose**: MariaDB database authentication

**Required Keys**:

```yaml
MARIADB_USERNAME: <string>     # Database user (e.g., "skoda")
MARIADB_PASSWORD: <string>     # Database password
```

**Ansible Vault Variables** (from central repo):

- `mariadb_username` → MARIADB_USERNAME
- `mariadb_password` → MARIADB_PASSWORD

**Example creation command**:

```bash
kubectl create secret generic mariadb-credentials \
  --from-literal=MARIADB_USERNAME='skoda' \
  --from-literal=MARIADB_PASSWORD='<secure-password>' \
  -n skoda
```

---

### 3. `mariadb-app-config` (Type: ConfigMap - patched by homelab gitops kustomization)

**Namespace**: `skoda`

**Purpose**: MariaDB connection configuration (non-sensitive)

**Keys**:

```yaml
MARIADB_DATABASE: "skoda"      # Database name
MARIADB_HOSTNAME: "mariadb.home.arpa"  # MariaDB host — prod default; dev overlay patches to mariadb.skoda-dev.svc.cluster.local
MARIADB_PORT: "3306"           # Database port
```

**Update procedure**: Edit the `mariadb-app-config` patch in the relevant overlay
(`k8s/overlays/prod/patch-configmap-prod.yaml` or `k8s/overlays/dev/patch-configmap-dev.yaml`)
and push — Argo CD will sync it automatically.

Do **not** update this via Ansible; Argo CD will overwrite any out-of-band changes on the next sync.

---

### 4. `graylog-credentials` (Type: Opaque)

**Namespace**: `skoda`

**Purpose**: Graylog server configuration for centralized logging

**Required Keys**:

```yaml
GRAYLOG_HOST: <string>     # Graylog server hostname or IP (e.g., "graylog.monitoring.svc.cluster.local")
```

**Ansible Vault Variables** (from central repo):

- `graylog_host` → GRAYLOG_HOST

**Note**: GRAYLOG_PORT is a static non-sensitive value in ConfigMap: `graylog-app-config` (set to "12201")

**Example creation command**:

```bash
kubectl create secret generic graylog-credentials \
  --from-literal=GRAYLOG_HOST='graylog.monitoring.svc.cluster.local' \
  -n skoda
```

---

## ConfigMaps (Non-Sensitive Configuration)

### `skoda-app-config`

Static application configuration (no Ansible overrides):

- `ENV`: pod environment (prod/dev)
- `LOG_LEVEL`: logging verbosity
- `SERVICE_TIMEOUT`: HTTP request timeout

### `mariadb-app-config`

Database connectivity configuration (MARIADB_HOSTNAME is Ansible-configurable per environment):

- `MARIADB_DATABASE`: database name (default: "skoda", configurable via `mariadb_database` Ansible variable)
- `MARIADB_HOSTNAME`: MariaDB service address (configurable via `mariadb_hostname` Ansible variable - CRITICAL FOR ENVIRONMENT DIFFERENCES)
- `MARIADB_PORT`: database port (default: 3306, configurable via `mariadb_port` Ansible variable)

### `graylog-app-config`

Graylog configuration (static values):

- `GRAYLOG_PORT`: static port (always 12201)
- Note: `GRAYLOG_HOST` comes from `graylog-credentials` secret

---

## Deployment Impact

All five services inject these secrets via `valueFrom.secretKeyRef`:

**Services affected**:

1. `skodaimporter` - requires all skoda-credentials, mariadb, graylog
2. `skodachargefinder` - requires mariadb, graylog
3. `skodachargecollector` - requires mariadb, graylog
4. `skodaupdatechargeprices` - requires mariadb, graylog
5. `skodachargefrontend` - requires mariadb, graylog

**Secret mount paths** (when using volumeMount):

- `/run/secrets/` - Kubernetes standard location for mounted secrets
- Accessed via `commons.load_secret()` in Python code

---

## Inter-Service Communication

Services communicate via **Kubernetes DNS** names:

| Service | URL | Port |
|---------|-----|------|
| skodaimporter | <http://skodaimporter.skoda.svc.cluster.local> | 80 |
| skodachargefinder | <http://skodachargefinder.skoda.svc.cluster.local> | 80 |
| skodachargecollector | <http://skodachargecollector.skoda.svc.cluster.local> | 80 |
| skodaupdatechargeprices | <http://skodaupdatechargeprices.skoda.svc.cluster.local> | 80 |
| skodachargefrontend | <http://skodachargefrontend.skoda.svc.cluster.local> | 80 |

These URLs are hard-coded in `commons.py`:

- `CHARGEFINDER_URL = "http://chargefinder/find-charges"` (short form, resolved within namespace)
- `CHARGECOLLECTOR_URL = "http://chargecollector/collect-charges"`
- etc.

---

## Validation Checklist

Before deployment, verify all secrets and configuration are correctly provisioned:

```bash
# Check all secrets exist
kubectl get secrets -n skoda

# Verify content of each secret (example)
kubectl get secret skoda-credentials -n skoda -o jsonpath='{.data.SKODA_USER}' | base64 -d

# Verify ConfigMaps exist
kubectl get configmaps -n skoda

# CRITICAL: Verify MARIADB_HOSTNAME is correctly set per environment
kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}'
# Expected output: mariadb.home.arpa (prod) or mariadb.skoda-dev.svc.cluster.local (dev)

# Check service discovery works (test with the actual MARIADB_HOSTNAME from above)
MARIADB_HOST=$(kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}')
kubectl run -it --rm debug --image=alpine --restart=Never -n skoda -- nslookup $MARIADB_HOST
```

---

## Update Procedure

To update any secret after initial deployment:

**Via Ansible** (recommended):

1. Update Ansible vault variable
2. Re-run central Ansible playbook to patch secret
3. Trigger pod restart: `kubectl rollout restart deployment/<deployment-name> -n skoda`

**Manual** (emergency):

```bash
kubectl create secret generic skoda-credentials \
  --from-literal=SKODA_USER='<new-value>' \
  --from-literal=SKODA_PASS='<new-value>' \
  -n skoda \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart pods to pick up new secrets
kubectl rollout restart deployment -n skoda
```

---

## Troubleshooting

### Secret not found errors

Check that the secret exists in the correct namespace:

```bash
kubectl describe secret skoda-credentials -n skoda
```

### ConfigMap not updated with mariadb_hostname

Verify the mariadb-app-config contains correct hostname:

```bash
kubectl get configmap mariadb-app-config -n skoda -o yaml
```

If MARIADB_HOSTNAME is wrong or missing, edit the configmap patch in `k8s/overlays/{prod,dev}/patch-configmap-{prod,dev}.yaml` and push — Argo CD will sync it.

### Pod failing to start with database connection errors

Check the MARIADB_HOSTNAME is resolvable:

```bash
MARIADB_HOST=$(kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}')
kubectl exec -it <pod-name> -n skoda -- nslookup $MARIADB_HOST

# If nslookup fails, the MariaDB service may be:
# 1. In a different namespace: use full name like mariadb.production.svc.cluster.local
# 2. External to cluster: use external hostname/IP
# 3. Not running: kubectl get pods -n <mariadb-namespace>
```

### Pod failing to start - all required secret keys are present

Verify all required secret keys are present:

```bash
kubectl get secret skoda-credentials -n skoda -o yaml
kubectl get secret mariadb-credentials -n skoda -o yaml
kubectl get secret graylog-credentials -n skoda -o yaml
```

### Service connectivity issues

Verify DNS resolution within cluster:

```bash
MARIADB_HOST=$(kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}')
kubectl exec -it <pod-name> -n skoda -- nslookup $MARIADB_HOST
```

---

## MariaDB Initialization

For fresh deployments, the database schema must be initialized using `sqldump/sqldump.sql` from the application repository. This should be done by:

1. An init container in the mariadb Pod (if MariaDB is also in-cluster)
2. Or manually before deployment (if using external MariaDB)

The schema creates three main tables:

- `rawlogs` - Raw vehicle telemetry
- `charge_events` - Individual charging start/stop events
- `charge_hours` - Aggregated hourly charging data

Credentials: `skoda` / (value from `mariadb-credentials` secret)
