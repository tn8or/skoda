# Skoda Kubernetes Migration: Central Ansible/ArgoCD Repository Setup Prompt

## OBJECTIVE

Set up the central Kubernetes cluster GitOps and secrets provisioning layer using Ansible + ArgoCD to deploy the Skoda Data Logger microservices platform to your Kubernetes cluster.

**Result**: Fully functional Skoda platform running in production and development environments with ArgoCD-managed GitOps and Ansible-provisioned secrets.

---

## PART 1: ANSIBLE VAULT VARIABLES SETUP

### Create Vault Files

Create encrypted vault files for each environment:

```bash
# Production secrets
ansible-vault create group_vars/k8s_prod/skoda-vault.yml

# Development secrets
ansible-vault create group_vars/k8s_dev/skoda-vault.yml
```

### Production Vault Variables

**File**: `group_vars/k8s_prod/skoda-vault.yml`

```yaml
# MySkoda API Credentials
skoda_user: "your_skoda_email@example.com"
skoda_pass: "your_skoda_api_password"

# Optional: Only set if using custom auth/vehicle configurations
skoda_auth: ""                    # Custom auth token (leave empty if not needed)
skoda_events: ""                  # Event filtering config (leave empty if not needed)
skoda_vehicle: ""                 # Vehicle VIN (leave empty if not needed)

# MariaDB Configuration (Production - typically external managed database)
# CRITICAL: Set this to your production database hostname
mariadb_hostname: "mariadb.production.svc.cluster.local"  # or your external prod DB
mariadb_username: "skoda"
mariadb_password: "secure_prod_mariadb_password_here"
mariadb_database: "skoda"
mariadb_port: "3306"

# Graylog Logging
graylog_host: "graylog.monitoring.svc.cluster.local"  # or your Graylog service name
```

### Development Vault Variables

**File**: `group_vars/k8s_dev/skoda-vault.yml`

**Option A: Development with in-cluster ephemeral MariaDB**

```yaml
# MySkoda API Credentials
skoda_user: "dev_skoda_email@example.com"
skoda_pass: "dev_skoda_api_password"

# Optional fields
skoda_auth: ""
skoda_events: ""
skoda_vehicle: ""

# MariaDB Configuration (Development - in-cluster, ephemeral)
# The dev overlay deploys a MariaDB pod with emptyDir storage
# Keep this hostname as-is for in-cluster dev MariaDB
mariadb_hostname: "mariadb.skoda.svc.cluster.local"
mariadb_username: "skoda"
mariadb_password: "skodapass"          # Matches mariadb-init-secret in dev overlay
mariadb_database: "skoda"
mariadb_port: "3306"

# Graylog Logging
graylog_host: "graylog-dev.monitoring.svc.cluster.local"  # or dev Graylog
```

**Option B: Development with external MariaDB**

```yaml
# If you prefer external database for dev instead of in-cluster
mariadb_hostname: "dev-db.example.com"
mariadb_username: "skoda"
mariadb_password: "dev_external_db_password"
mariadb_database: "skoda"
mariadb_port: "3306"

# ... rest of config same as Option A
```

### To Edit Vault Files

```bash
# Edit with vault encryption active
ansible-vault edit group_vars/k8s_prod/skoda-vault.yml
ansible-vault edit group_vars/k8s_dev/skoda-vault.yml
```

---

## PART 2: CREATE KUBERNETES SECRETS PROVISIONING PLAYBOOK

### Playbook Structure

Create a new playbook: `plays/provision-skoda-secrets.yml`

**Requirements**:

- Module: `community.kubernetes.k8s` (install via `ansible-galaxy collection install community.kubernetes`)
- Idempotent: Can run multiple times safely
- Supports both prod and dev environments
- Validates all secrets created successfully
- Triggers pod restarts on secret updates

### Minimal Playbook Template

```yaml
---
- name: Provision Skoda Platform Secrets to Kubernetes
  hosts: k8s_control
  gather_facts: no
  vars:
    skoda_namespace: "skoda"

  tasks:
  # ==================== Create Namespace ====================
  - name: Create skoda namespace
    kubernetes.core.k8s:
      name: "{{ skoda_namespace }}"
      api_version: v1
      kind: Namespace
      state: present

  # ==================== Create skoda-credentials Secret ====================
  - name: Create skoda-credentials secret
    kubernetes.core.k8s:
      namespace: "{{ skoda_namespace }}"
      state: present
      definition:
        apiVersion: v1
        kind: Secret
        metadata:
          name: skoda-credentials
        type: Opaque
        stringData:
          SKODA_USER: "{{ skoda_user }}"
          SKODA_PASS: "{{ skoda_pass }}"
          SKODA_AUTH: "{{ skoda_auth | default('') }}"
          SKODA_EVENTS: "{{ skoda_events | default('') }}"
          SKODA_VEHICLE: "{{ skoda_vehicle | default('') }}"

  # ==================== Create mariadb-credentials Secret ====================
  - name: Create mariadb-credentials secret
    kubernetes.core.k8s:
      namespace: "{{ skoda_namespace }}"
      state: present
      definition:
        apiVersion: v1
        kind: Secret
        metadata:
          name: mariadb-credentials
        type: Opaque
        stringData:
          MARIADB_USERNAME: "{{ mariadb_username }}"
          MARIADB_PASSWORD: "{{ mariadb_password }}"

  # ==================== Create graylog-credentials Secret ====================
  - name: Create graylog-credentials secret
    kubernetes.core.k8s:
      namespace: "{{ skoda_namespace }}"
      state: present
      definition:
        apiVersion: v1
        kind: Secret
        metadata:
          name: graylog-credentials
        type: Opaque
        stringData:
          GRAYLOG_HOST: "{{ graylog_host }}"

  # ==================== Patch mariadb-app-config ConfigMap ====================
  - name: Patch mariadb-app-config with Ansible-provided hostname
    kubernetes.core.k8s:
      namespace: "{{ skoda_namespace }}"
      state: present
      definition:
        apiVersion: v1
        kind: ConfigMap
        metadata:
          name: mariadb-app-config
        data:
          MARIADB_DATABASE: "{{ mariadb_database | default('skoda') }}"
          MARIADB_HOSTNAME: "{{ mariadb_hostname }}"
          MARIADB_PORT: "{{ mariadb_port | default('3306') }}"

  # ==================== Validate Secrets Created ====================
  - name: Validate skoda-credentials secret
    kubernetes.core.k8s_info:
      kind: Secret
      namespace: "{{ skoda_namespace }}"
      name: skoda-credentials
    register: skoda_credentials_result
    failed_when:
      - skoda_credentials_result.resources | length == 0
      - "'SKODA_USER' not in (skoda_credentials_result.resources[0].data | default({}))"
      - "'SKODA_PASS' not in (skoda_credentials_result.resources[0].data | default({}))"

  - name: Validate mariadb-credentials secret
    kubernetes.core.k8s_info:
      kind: Secret
      namespace: "{{ skoda_namespace }}"
      name: mariadb-credentials
    register: mariadb_credentials_result
    failed_when:
      - mariadb_credentials_result.resources | length == 0
      - "'MARIADB_USERNAME' not in (mariadb_credentials_result.resources[0].data | default({}))"
      - "'MARIADB_PASSWORD' not in (mariadb_credentials_result.resources[0].data | default({}))"

  - name: Validate graylog-credentials secret
    kubernetes.core.k8s_info:
      kind: Secret
      namespace: "{{ skoda_namespace }}"
      name: graylog-credentials
    register: graylog_credentials_result
    failed_when:
      - graylog_credentials_result.resources | length == 0
      - "'GRAYLOG_HOST' not in (graylog_credentials_result.resources[0].data | default({}))"

  - name: Validate mariadb-app-config ConfigMap
    kubernetes.core.k8s_info:
      kind: ConfigMap
      namespace: "{{ skoda_namespace }}"
      name: mariadb-app-config
    register: mariadb_config_result
    failed_when:
      - mariadb_config_result.resources | length == 0
      - "'MARIADB_HOSTNAME' not in (mariadb_config_result.resources[0].data | default({}))"

  - name: Display provisioned configuration
    debug:
      msg: |
        Skoda Platform Secrets Provisioned Successfully:
        =============================================

        Namespace: {{ skoda_namespace }}

        Secrets Created:
        - skoda-credentials
          - SKODA_USER: ✓
          - SKODA_PASS: ✓
          - SKODA_AUTH: ✓ (optional)
          - SKODA_EVENTS: ✓ (optional)
          - SKODA_VEHICLE: ✓ (optional)

        - mariadb-credentials
          - MARIADB_USERNAME: {{ mariadb_username }}
          - MARIADB_PASSWORD: ✓ (encrypted)

        - graylog-credentials
          - GRAYLOG_HOST: {{ graylog_host }}

        ConfigMaps Created/Updated:
        - mariadb-app-config
          - MARIADB_DATABASE: {{ mariadb_database | default('skoda') }}
          - MARIADB_HOSTNAME: {{ mariadb_hostname }}
          - MARIADB_PORT: {{ mariadb_port | default('3306') }}

        Next: Deploy ArgoCD Application for Skoda
```

### Run the Playbook

```bash
# For production
ansible-playbook -i inventory plays/provision-skoda-secrets.yml \
  -l k8s_prod \
  --vault-password-file=~/.ansible/vault_password

# For development
ansible-playbook -i inventory plays/provision-skoda-secrets.yml \
  -l k8s_dev \
  --vault-password-file=~/.ansible/vault_password
```

---

## PART 3: UNDERSTAND THE 5 SKODA SERVICES

Before deploying, understand what will be deployed by ArgoCD:

### Service Architecture

The Skoda platform consists of **5 microservices** running in the `skoda` namespace:

| Service | Purpose | Replicas | Port | Description |
|---------|---------|----------|------|-------------|
| **skodaimporter** | MySkoda API Importer | 1 (prod) | 80 | Connects to MySkoda API, fetches vehicle data, detects charging events |
| **skodachargefinder** | Charge Event Detector | 3 (prod), 1 (dev) | 80 | Analyzes raw logs to identify charge start/stop events |
| **skodachargecollector** | Charge Data Aggregator | 3 (prod), 1 (dev) | 80 | Processes charge events into hourly summaries, stores in database |
| **skodaupdatechargeprices** | Pricing Service | 2 (prod), 1 (dev) | 80 | Updates charge pricing information (kWh rates, cost analysis) |
| **skodachargefrontend** | Web Dashboard | 3 (prod), 1 (dev) | 80 | Displays charging history, statistics, analytics to users |

### Data Flow (What Gets Deployed)

```
┌─────────────────────────────────────────────────────────────────┐
│  Kubernetes Cluster (skoda namespace)                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────────┐                                           │
│  │ skodaimporter   │ ◄──── MySkoda API (external)             │
│  │ (1 pod)         │       Fetches: vehicle status, charging  │
│  └────────┬────────┘       data, health info                  │
│           │ HTTP POST                                          │
│           ▼                                                     │
│  ┌──────────────────────────┐    ┌─────────────────────────┐  │
│  │ skodachargefinder (1-3)  │───►│ MariaDB                 │  │
│  │ Detects charge events    │    │ (external or dev        │  │
│  └──────────────────────────┘    │  in-cluster)            │  │
│           │                       │                         │  │
│           ▼                       │ Tables:                 │  │
│  ┌──────────────────────────┐    │ - rawlogs               │  │
│  │ skodachargecollector (1-3)   │ - charge_events         │  │
│  │ Aggregates to hourly data    │ - charge_hours          │  │
│  └──────────────────────────┘    └─────────────────────────┘  │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────────────┐                                 │
│  │ skodaupdatechargeprices  │ ◄──── Pricing APIs (external) │
│  │ (1-2 pods)               │       Updates: rates, costs    │
│  └──────────────────────────┘                                 │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────────────┐                                 │
│  │ skodachargefrontend (1-3) │ ◄──── Users via Browser        │
│  │ Web UI Dashboard          │       Displays: history,       │
│  └──────────────────────────┘       stats, analysis          │
│                                                                 │
│  All services ──► Graylog (external) for centralized logging  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Service Dependencies

```
Kubernetes Services (DNS-discoverable):
- skodaimporter.skoda.svc.cluster.local:80
- skodachargefinder.skoda.svc.cluster.local:80
- skodachargecollector.skoda.svc.cluster.local:80
- skodaupdatechargeprices.skoda.svc.cluster.local:80
- skodachargefrontend.skoda.svc.cluster.local:80
- mariadb.skoda.svc.cluster.local:3306 (prod: external, dev: in-cluster)
```

### Container Images

All images will be pulled from GitHub Container Registry:

```
ghcr.io/tn8or/skoda/skodaimporter:latest
ghcr.io/tn8or/skoda/skodachargefinder:latest
ghcr.io/tn8or/skoda/skodachargecollector:latest
ghcr.io/tn8or/skoda/skodaupdatechargeprices:latest
ghcr.io/tn8or/skoda/skodachargefrontend:latest
```

### Expected Pod Topology After Deployment

**Development** (minimal, 1 replica each + dev MariaDB):

```
skoda namespace:
- Pod: skodaimporter-XXXXX (1/1 Running)
- Pod: skodachargefinder-XXXXX (1/1 Running)
- Pod: skodachargecollector-XXXXX (1/1 Running)
- Pod: skodaupdatechargeprices-XXXXX (1/1 Running)
- Pod: skodachargefrontend-XXXXX (1/1 Running)
- Pod: mariadb-XXXXX (1/1 Running) ← ephemeral storage, dev only
Total: 6 pods
```

**Production** (high availability, multiple replicas):

```
skoda namespace:
- Pod: skodaimporter-XXXXX (1/1 Running)  [1 replica]
- Pod: skodachargefinder-XXXXX (1/1 Running)  [3 replicas]
- Pod: skodachargefinder-XXXXX (1/1 Running)
- Pod: skodachargefinder-XXXXX (1/1 Running)
- Pod: skodachargecollector-XXXXX (1/1 Running)  [3 replicas]
- Pod: skodachargecollector-XXXXX (1/1 Running)
- Pod: skodachargecollector-XXXXX (1/1 Running)
- Pod: skodaupdatechargeprices-XXXXX (1/1 Running)  [2 replicas]
- Pod: skodaupdatechargeprices-XXXXX (1/1 Running)
- Pod: skodachargefrontend-XXXXX (1/1 Running)  [3 replicas]
- Pod: skodachargefrontend-XXXXX (1/1 Running)
- Pod: skodachargefrontend-XXXXX (1/1 Running)
Total: 12 pods (no MariaDB pod; external database)
```

### Health Checks & Readiness

Each pod has:

- **Liveness Probe**: Checks `GET /` every 30s (restarts pod after 3 failures)
- **Readiness Probe**: Checks `GET /` every 10s (removes from load balancing after 2 failures)
- **Security Context**: Runs as non-root user (UID 1000)
- **Resource Limits**: 256Mi memory request, 512Mi limit; 100m CPU request, 500m limit

---

## PART 3: DEPLOY ARGOCD APPLICATION

### Apply ArgoCD Application Manifest

The Skoda repository already includes a ready-to-apply ArgoCD Application manifest.

```bash
# Download and apply the manifest
kubectl apply -f https://raw.githubusercontent.com/tn8or/skoda/main/k8s/ArgoCD-Application.yaml

# Or apply from your local clone
kubectl apply -f /path/to/skoda/k8s/ArgoCD-Application.yaml
```

### Monitor Sync Status

```bash
# Check application status
argocd app get skoda

# Watch sync progress
argocd app wait skoda --sync

# Force sync if needed
argocd app sync skoda

# View detailed status
argocd app status skoda
```

### Expected Output

```
NAME     CLUSTER                         NAMESPACE  PROJECT  STATUS     HEALTH  SYNCPOLICY
skoda    https://kubernetes.default.svc  skoda      default  Synced     Healthy Automatic
```

---

## PART 4: VALIDATION TASKS

### Create Validation Playbook

Create: `plays/validate-skoda-deployment.yml`

```yaml
---
- name: Validate Skoda Deployment
  hosts: k8s_control
  gather_facts: no
  vars:
    skoda_namespace: "skoda"

  tasks:
  - name: Check all secrets exist
    kubernetes.core.k8s_info:
      kind: Secret
      namespace: "{{ skoda_namespace }}"
    register: secrets_result

  - name: Display secrets
    debug:
      msg: "Secrets: {{ secrets_result.resources | map(attribute='metadata.name') | list }}"

  - name: Check all deployments
    kubernetes.core.k8s_info:
      kind: Deployment
      namespace: "{{ skoda_namespace }}"
    register: deployments_result

  - name: Verify 5 deployments exist
    assert:
      that:
        - deployments_result.resources | length >= 5
      fail_msg: "Expected at least 5 deployments, found {{ deployments_result.resources | length }}"

  - name: Verify expected service deployments
    assert:
      that:
        - "'skodaimporter' in (deployments_result.resources | map(attribute='metadata.name') | list)"
        - "'skodachargefinder' in (deployments_result.resources | map(attribute='metadata.name') | list)"
        - "'skodachargecollector' in (deployments_result.resources | map(attribute='metadata.name') | list)"
        - "'skodaupdatechargeprices' in (deployments_result.resources | map(attribute='metadata.name') | list)"
        - "'skodachargefrontend' in (deployments_result.resources | map(attribute='metadata.name') | list)"
      fail_msg: "Not all expected Skoda services are deployed. Found: {{ deployments_result.resources | map(attribute='metadata.name') | list }}"

  - name: Check all pods are running
    kubernetes.core.k8s_info:
      kind: Pod
      namespace: "{{ skoda_namespace }}"
    register: pods_result

  - name: Verify all pods ready
    assert:
      that:
        - pods_result.resources | selectattr('status.conditions[] | selectattr("type", "equalto", "Ready") | selectattr("status", "equalto", "True")') | length > 0
      fail_msg: "Some pods are not in Ready state"

  - name: Display pod status
    debug:
      msg: "Pods: {{ pods_result.resources | map(attribute='metadata.name') | list }}"

  - name: Check MARIADB_HOSTNAME is set correctly
    kubernetes.core.k8s_info:
      kind: ConfigMap
      namespace: "{{ skoda_namespace }}"
      name: mariadb-app-config
    register: mariadb_config

  - name: Display MariaDB hostname
    debug:
      msg: "MARIADB_HOSTNAME: {{ mariadb_config.resources[0].data.MARIADB_HOSTNAME }}"

  - name: Check ArgoCD Application status
    shell: |
      argocd app get skoda -o json | jq '.status.operationState.phase'
    register: argocd_status
    ignore_errors: yes

  - name: Display validation results
    debug:
      msg: |
        Skoda Deployment Validation Results
        ====================================

        Secrets Found: {{ secrets_result.resources | map(attribute='metadata.name') | list }}

        Deployments: {{ deployments_result.resources | length }}
        Services Deployed:
        {% for deployment in deployments_result.resources %}
          - {{ deployment.metadata.name }}: {{ deployment.status.readyReplicas | default(0) }}/{{ deployment.status.replicas | default(0) }} ready
        {% endfor %}

        Expected Services (must be running):
        - skodaimporter: Fetches vehicle data from MySkoda API
        - skodachargefinder: Detects charging events
        - skodachargecollector: Aggregates charging data
        - skodaupdatechargeprices: Updates pricing information
        - skodachargefrontend: Web UI dashboard

        Pods Ready: {{ pods_result.resources | length }}

        MariaDB Configuration:
        - Hostname: {{ mariadb_config.resources[0].data.MARIADB_HOSTNAME }}
        - Database: {{ mariadb_config.resources[0].data.MARIADB_DATABASE }}
        - Port: {{ mariadb_config.resources[0].data.MARIADB_PORT }}

        ArgoCD Application Status: {{ argocd_status.stdout | default('Unable to retrieve') }}
```

### Run Validation

```bash
# Production
ansible-playbook -i inventory plays/validate-skoda-deployment.yml \
  -l k8s_prod \
  --vault-password-file=~/.ansible/vault_password

# Development
ansible-playbook -i inventory plays/validate-skoda-deployment.yml \
  -l k8s_dev \
  --vault-password-file=~/.ansible/vault_password
```

---

## PART 5: DEVELOPMENT ENVIRONMENT SPECIAL SETUP

### In-Cluster Ephemeral MariaDB

For development, the k8s/overlays/dev/ includes a MariaDB deployment with:

- **Ephemeral storage**: `emptyDir` (data lost on pod restart)
- **Purpose**: Testing and development only
- **Credentials**: Hardcoded for dev (never use in production)

This MariaDB is **automatically deployed** when you apply the dev overlay via ArgoCD.

### Development MariaDB Configuration

**Vault Variable** (automatically from `group_vars/k8s_dev/skoda-vault.yml`):

```yaml
mariadb_hostname: "mariadb.skoda.svc.cluster.local"
mariadb_username: "skoda"
mariadb_password: "skodapass"
```

**Result**: All Skoda services connect to the in-cluster dev MariaDB automatically.

### Manual Testing of Dev MariaDB

```bash
# Port-forward to MariaDB
kubectl port-forward -n skoda svc/mariadb 3306:3306

# Connect from another terminal
mariadb -h 127.0.0.1 -u skoda -pskodapass skoda

# Or use kubectl exec
kubectl exec -it -n skoda deployment/mariadb -- mariadb -u skoda -pskodapass skoda
```

### Initialize Database Schema (Manual)

If the schema isn't auto-initialized:

```bash
# Get the sqldump from Skoda repo
curl -O https://raw.githubusercontent.com/tn8or/skoda/main/sqldump/sqldump.sql

# Apply schema to dev MariaDB
kubectl exec -i -n skoda deployment/mariadb -- \
  mariadb -u skoda -pskodapass skoda < sqldump.sql
```

---

## PART 6: INTEGRATION WITH EXISTING GITOPS

### Option A: Helm + ArgoCD

Add to your Helm values or Application:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: skoda
spec:
  # ... reference the Skoda Application
  source:
    repoURL: https://github.com/tn8or/skoda
    path: k8s/overlays/prod
```

### Option B: Kustomize + ArgoCD

Add to your root `kustomization.yaml`:

```yaml
resources:
- https://raw.githubusercontent.com/tn8or/skoda/main/k8s/ArgoCD-Application.yaml
```

### Option C: ApplicationSet for Multi-Environment

The Skoda repository already includes an ApplicationSet for managing prod/dev:

```bash
kubectl apply -f https://raw.githubusercontent.com/tn8or/skoda/main/k8s/ArgoCD-Application.yaml
```

---

## PART 7: RECURRING MAINTENANCE TASKS

### Add to Daily/Weekly Playbook

```yaml
---
- name: Skoda Platform Health Checks
  hosts: k8s_control
  gather_facts: no
  vars:
    skoda_namespace: "skoda"

  tasks:
  - name: Check Skoda deployment health
    kubernetes.core.k8s_info:
      kind: Deployment
      namespace: "{{ skoda_namespace }}"
    register: deployments

  - name: Alert if any deployment replicas not ready
    assert:
      that:
        - item.status.readyReplicas | default(0) == item.status.replicas | default(0)
      fail_msg: "Deployment {{ item.metadata.name }} has replicas not ready"
    loop: "{{ deployments.resources }}"
    ignore_errors: yes

  - name: Check ArgoCD Application sync status
    shell: |
      argocd app get skoda -o json | jq '.status.sync.status'
    register: sync_status
    ignore_errors: yes

  - name: Alert if out of sync
    assert:
      that:
        - "'Synced' in sync_status.stdout"
      fail_msg: "Skoda Application is out of sync: {{ sync_status.stdout }}"
    ignore_errors: yes
```

### Secret Rotation Procedure

```bash
# 1. Update vault variables
ansible-vault edit group_vars/k8s_prod/skoda-vault.yml

# 2. Re-run provision playbook
ansible-playbook -i inventory plays/provision-skoda-secrets.yml \
  -l k8s_prod \
  --vault-password-file=~/.ansible/vault_password

# 3. Verify secrets updated
kubectl get secret skoda-credentials -n skoda -o jsonpath='{.data.SKODA_USER}' | base64 -d

# 4. Trigger pod restarts (optional, depends on if secret watchers are configured)
kubectl rollout restart deployment -n skoda
```

---

## PART 8: TROUBLESHOOTING

### Secrets Not Created

```bash
# Check namespace exists
kubectl get namespace skoda

# Check RBAC permissions
kubectl auth can-i create secrets --as=system:serviceaccount:skoda:skoda-services -n skoda

# Manually check secret
kubectl get secret skoda-credentials -n skoda -o yaml
```

### MARIADB_HOSTNAME Incorrect

```bash
# Verify ConfigMap
kubectl get configmap mariadb-app-config -n skoda -o jsonpath='{.data.MARIADB_HOSTNAME}'

# Check pod can resolve it
kubectl exec -it -n skoda <pod-name> -- nslookup <mariadb_hostname>

# Check MariaDB pod is running (for dev)
kubectl get pods -n skoda | grep mariadb
```

### Pods Failing to Start

```bash
# Check pod logs
kubectl logs -n skoda <pod-name> -c <container-name>

# Check pod events
kubectl describe pod -n skoda <pod-name>

# Check secret values are readable
kubectl exec -it -n skoda <pod-name> -- env | grep SKODA_
```

### ArgoCD Application Not Syncing

```bash
# Check application status
argocd app get skoda

# Check manifest differences
argocd app diff skoda

# Force sync
argocd app sync skoda --force

# Check ArgoCD logs
kubectl logs -n argocd deployment/argocd-application-controller
```

---

## PART 9: PRODUCTION CHECKLIST

Before deploying to production, verify:

- [ ] Production vault variables created with real credentials
- [ ] `mariadb_hostname` points to production database
- [ ] `graylog_host` points to production Graylog instance
- [ ] All secrets provisioned to production namespace
- [ ] **All 5 services running**:
  - [ ] skodaimporter (1 replica)
  - [ ] skodachargefinder (3 replicas)
  - [ ] skodachargecollector (3 replicas)
  - [ ] skodaupdatechargeprices (2 replicas)
  - [ ] skodachargefrontend (3 replicas)
- [ ] All pods with correct replica count (12 total for prod)
- [ ] Services can communicate (DNS resolution working)
- [ ] Database connectivity verified (run SQL query)
- [ ] Logging to Graylog verified (check logs appear)
- [ ] ArgoCD Application synced (no drift)
- [ ] Health endpoints responding (GET /)
- [ ] Alerts/monitoring configured for all services
- [ ] Rollback procedure tested

---

## PART 10: REPOSITORY ARTIFACTS

After completing this setup, you should have:

### Playbooks Created

- `plays/provision-skoda-secrets.yml` - Creates secrets from vault variables
- `plays/validate-skoda-deployment.yml` - Validates deployment health

### Vault Files Created

- `group_vars/k8s_prod/skoda-vault.yml` - Production secrets (encrypted)
- `group_vars/k8s_dev/skoda-vault.yml` - Development secrets (encrypted)

### Configuration

- Inventory configured with `k8s_prod` and `k8s_dev` groups
- Ansible vars configured to use `community.kubernetes` collection

### Documentation

- This prompt + implementation details
- Runbook for common operations
- Troubleshooting guide

---

## CRITICAL NOTES

### MARIADB_HOSTNAME is Environment-Critical

- **Production**: Set to your managed database endpoint (e.g., `mariadb.production.svc.cluster.local` or `db.example.com`)
- **Development**: Automatically `mariadb.skoda.svc.cluster.local` (in-cluster ephemeral)
- **NEVER hard-code** in application code
- **MUST be configurable** via Ansible variables

### Secrets vs ConfigMaps

- **Secrets**: Encrypted in vault, should be sensitive (credentials, API keys)
- **ConfigMaps**: Can be environment-specific (hostnames, ports, non-sensitive config)
- The split allows different secret rotation schedules per environment

### Dev MariaDB Ephemeral Storage

- Data in dev MariaDB is **NOT persistent**
- Pod restart = data loss (intended for testing)
- For persistent dev data, replace `emptyDir` with `PersistentVolumeClaim`

---

## FINAL CHECKLIST

Complete these steps in order:

1. [ ] Create vault files: `group_vars/k8s_*/skoda-vault.yml`
2. [ ] Fill vault files with environment-specific variables
3. [ ] Create `plays/provision-skoda-secrets.yml`
4. [ ] Run provisioning playbook for dev
5. [ ] Validate dev deployment
6. [ ] Run provisioning playbook for prod
7. [ ] Validate prod deployment
8. [ ] Set up recurring health check playbook
9. [ ] Document secret rotation procedure
10. [ ] Test rollback scenarios
11. [ ] Configure monitoring/alerts
12. [ ] Archive this prompt + final playbook code

---

## QUESTIONS FOR FINAL VALIDATION

After completing setup, answer these questions:

1. What is the current Skoda Application sync status in ArgoCD?
2. How many pods are running in the skoda namespace?
3. List the 5 Skoda services deployed and their replica counts:
   - skodaimporter: __ replicas
   - skodachargefinder: __ replicas
   - skodachargecollector: __ replicas
   - skodaupdatechargeprices: __ replicas
   - skodachargefrontend: __ replicas
4. What is the MARIADB_HOSTNAME for production?
5. When was the last secret rotation?
6. What's the procedure to rotate secrets without downtime?
7. Can you connect to MariaDB and verify schema exists?
8. Are all Skoda services healthy and responding to GET /?
9. Is Graylog receiving logs from all services?
10. What would you do if ArgoCD shows drift between Git and cluster state?

---

**END OF PROMPT**

---

## Reference Documentation

- **Skoda K8s Manifests**: <https://github.com/tn8or/skoda/tree/main/k8s>
- **Secrets Contract**: <https://github.com/tn8or/skoda/blob/main/k8s/SECRETS_CONTRACT.md>
- **Ansible Variables Reference**: <https://github.com/tn8or/skoda/blob/main/k8s/ANSIBLE_VARIABLES.md>
- **ArgoCD Documentation**: <https://argo-cd.readthedocs.io/>
- **Ansible Kubernetes Collection**: <https://ansible-collections.github.io/community.kubernetes/>
