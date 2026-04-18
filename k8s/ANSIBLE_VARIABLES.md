# Ansible Vault Variables for Skoda Kubernetes Deployment

This document specifies the exact Ansible vault variables required for provisioning the Skoda platform to Kubernetes.

## Vault Variables Required

### MySkoda API Credentials

```yaml
skoda_user: "your_skoda_email@example.com"
skoda_pass: "your_skoda_api_password"

# Optional: Only set if using custom auth/vehicle configurations
skoda_auth: ""                    # Custom auth token (leave empty if not needed)
skoda_events: ""                  # Event filtering config (leave empty if not needed)
skoda_vehicle: ""                 # Vehicle VIN (leave empty if not needed)
```

### MariaDB Database Credentials & Configuration

```yaml
# MariaDB Authentication (stored in Secret: mariadb-credentials)
mariadb_username: "skoda"
mariadb_password: "secure_mariadb_password_here"

# MariaDB Hostname (stored in ConfigMap: mariadb-app-config)
# CRITICAL: This value can differ per environment (prod vs dev)
# Examples:
#   - In-cluster: "mariadb.skoda.svc.cluster.local"
#   - In different namespace: "mariadb.production.svc.cluster.local"
#   - External: "db.example.com"
mariadb_hostname: "mariadb.skoda.svc.cluster.local"

# Optional: MariaDB port and database name
mariadb_port: "3306"              # (optional, defaults to 3306)
mariadb_database: "skoda"         # (optional, defaults to "skoda")
```

### Graylog Logging Configuration

```yaml
# Graylog Server (stored in Secret: graylog-credentials)
graylog_host: "graylog.monitoring.svc.cluster.local"  # or your Graylog service name
```

## Environment-Specific Overrides

If using `group_vars` with environment separation:

### Production Variables

**File**: `group_vars/k8s_prod/skoda-vault.yml`

```yaml
mariadb_hostname: "mariadb.production.svc.cluster.local"  # or prod external DB
mariadb_username: "skoda"
mariadb_password: "<prod-password>"

skoda_user: "<prod-user>"
skoda_pass: "<prod-pass>"

graylog_host: "graylog.monitoring.svc.cluster.local"
```

### Development Variables

**File**: `group_vars/k8s_dev/skoda-vault.yml`

**For development with in-cluster MariaDB (ephemeral)**:

```yaml
# Dev uses in-cluster ephemeral MariaDB deployed via k8s/overlays/dev/
mariadb_hostname: "mariadb.skoda.svc.cluster.local"
mariadb_username: "skoda"
mariadb_password: "skodapass"          # Matches mariadb-init-secret in dev overlay

skoda_user: "<dev-user>"
skoda_pass: "<dev-pass>"

graylog_host: "graylog-dev.monitoring.svc.cluster.local"
```

**For development with external MariaDB**:

```yaml
# If you prefer external dev database instead of in-cluster
mariadb_hostname: "dev-db.example.com"
mariadb_username: "skoda"
mariadb_password: "<dev-external-password>"

skoda_user: "<dev-user>"
skoda_pass: "<dev-pass>"

graylog_host: "graylog-dev.monitoring.svc.cluster.local"
```

**Key differences for dev**:

- Ephemeral storage: Data is lost on pod restart (intended for testing)
- Single replica: Only one MariaDB pod (vs production which may use managed database)
- Minimal resources: 256Mi memory request (vs production limits)
- Simplified credentials: Password hardcoded in dev overlay (never do this in prod)

## Ansible Playbook Implementation

The playbook that provisions these variables into Kubernetes should:

1. **Create Secret: skoda-credentials**
   - Keys: SKODA_USER, SKODA_PASS, SKODA_AUTH (optional), SKODA_EVENTS (optional), SKODA_VEHICLE (optional)
   - Source: `skoda_user`, `skoda_pass`, `skoda_auth`, `skoda_events`, `skoda_vehicle`

2. **Create Secret: mariadb-credentials**
   - Keys: MARIADB_USERNAME, MARIADB_PASSWORD
   - Source: `mariadb_username`, `mariadb_password`

3. **Create Secret: graylog-credentials**
   - Keys: GRAYLOG_HOST
   - Source: `graylog_host`

4. **Create ConfigMap: mariadb-app-config** (patched via Kustomize overlay)
   - Keys: MARIADB_HOSTNAME, MARIADB_DATABASE, MARIADB_PORT
   - Source: `mariadb_hostname`, `mariadb_database`, `mariadb_port`
   - Implementation: The base manifests include a default MARIADB_HOSTNAME; Kustomize overlays patch it per environment
   - The Ansible playbook should patch this ConfigMap with the correct value for the current environment

5. **Apply ArgoCD Application**
   - Reference: `https://github.com/tn8or/skoda/blob/main/k8s/ArgoCD-Application.yaml`
   - Reconcile until all Skoda services are running

## Validation Checklist for Ansible Playbook

After running the playbook, verify:

```bash
# 1. All secrets created
kubectl get secrets -n skoda
# Expected output includes: skoda-credentials, mariadb-credentials, graylog-credentials

# 2. All ConfigMaps created
kubectl get configmaps -n skoda
# Expected output includes: skoda-app-config, mariadb-app-config, graylog-app-config

# 3. Verify MARIADB_HOSTNAME is correct for current environment
kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}'
# Expected: Should match your mariadb_hostname variable value

# 4. All deployments running
kubectl get deployments -n skoda
# Expected: 5 deployments (importer, chargefinder, chargecollector, priceupdate, frontend)

# 5. All pods in Running state
kubectl get pods -n skoda
# Expected: 5 pods Ready (prod has more due to higher replicas)

# 6. ArgoCD Application synced
argocd app get skoda
# Expected: Sync Status = Synced, Health Status = Healthy
```

## Secret Rotation Procedure

When updating Ansible vault variables:

```yaml
# 1. Update vault variables
ansible-vault edit group_vars/k8s_cluster/skoda-vault.yml

# 2. Re-run provisioning playbook
ansible-playbook -i inventory plays/provision-skoda-secrets.yml -k

# 3. Verify secrets updated
kubectl get secret skoda-credentials -n skoda -o jsonpath='{.data.SKODA_USER}' | base64 -d

# 4. Trigger pod restarts to pick up new secrets
kubectl rollout restart deployment -n skoda
```

## Integration with Kustomize Overlays

The Kustomize overlays in the Skoda repository support Ansible-driven ConfigMap updates:

**How it works**:

1. Base manifests (`k8s/base/`) define all resources with sensible defaults
2. Overlays (`k8s/overlays/prod/` and `k8s/overlays/dev/`) include environment-specific ConfigMap patches
3. Ansible playbook patches the ConfigMap with Ansible variables before ArgoCD applies manifests
4. Result: Same code, different configurations per environment

**Example for prod**:

- Ansible variable `mariadb_hostname` = "mariadb.production.svc.cluster.local"
- Overlay applies this value to ConfigMap MARIADB_HOSTNAME
- All pods in prod connect to production MariaDB

## Important Notes

### MARIADB_HOSTNAME is Environment-Critical

- **Do NOT hard-code** this value in application code
- **Must be** configurable per environment
- **Examples of valid values**:
  - In-cluster: `mariadb.skoda.svc.cluster.local`
  - Different namespace: `mariadb.production.svc.cluster.local`
  - External database: `db.example.com`, `db.mycompany.net`, or IP address

### Secret vs ConfigMap Split

- **Secrets** (encrypted in vault): Credentials, API keys
- **ConfigMaps** (environment-specific): Non-sensitive config like hostnames, ports
- The split allows different secret values in prod vs dev while keeping ConfigMaps patchable via Kustomize

### Optional Fields

If `skoda_auth`, `skoda_events`, `skoda_vehicle` are not set in vault:

- Set them to empty string: `""`
- Or omit them entirely (Kubernetes will mark them as optional)
- Deployments will continue to work without these keys in the secret

## Troubleshooting

### Playbook fails with "Secret not found"

Ensure the namespace exists:

```bash
kubectl create namespace skoda
```

### Pods fail to start: "ConfigMap not found: mariadb-app-config"

The overlay ConfigMap patch may not have been applied. Verify:

```bash
kubectl get configmap mariadb-app-config -n skoda -o yaml
```

### MARIADB_HOSTNAME doesn't match environment

The Ansible variable may not match the overlay value. Check:

```bash
# Check what Ansible set
grep mariadb_hostname group_vars/*/skoda-vault.yml

# Check what Kubernetes has
kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}'

# These should match
```

### Pods can't connect to MariaDB

Verify MariaDB service is accessible at the configured hostname:

```bash
MARIADB_HOST=$(kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}')
kubectl run -it --rm debug --image=mariadb:latest --restart=Never -- \
  mariadb -h $MARIADB_HOST -u skoda -p -e "SELECT 1"
```
