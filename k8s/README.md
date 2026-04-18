# Kubernetes Manifests for Skoda

This repository provides deployable Kubernetes manifests using Kustomize.

Central Argo CD ownership model:

- Application/ApplicationSet objects are owned in homelab/localansible.
- This repository only provides workload manifests under k8s/base and k8s/overlays/*.
- Production source for Argo CD:
  - repoURL: <https://github.com/tn8or/skoda>
  - targetRevision: main
  - path: k8s/overlays/prod
  - destination namespace: skoda

## Folder Structure

```text
k8s/
├── base/
│   ├── configmap-apps.yaml
│   ├── deployments.yaml
│   ├── kustomization.yaml
│   ├── namespace.yaml
│   └── services.yaml
└── overlays/
    ├── dev/
    │   ├── kustomization.yaml
    │   └── patch-deployments.yaml
    └── prod/
        ├── kustomization.yaml
        └── patch-deployments.yaml
```

## Overlays

- k8s/base: shared resources (namespace-safe base Deployments, Services, ConfigMaps).
- k8s/overlays/prod: production replicas/resources.
- k8s/overlays/dev: lower replicas/resources for development.

Render manifests locally:

```bash
kustomize build k8s/overlays/prod
kustomize build k8s/overlays/dev
```

Apply manually if needed:

```bash
kubectl apply -k k8s/overlays/prod
# or
kubectl apply -k k8s/overlays/dev
```

## Secret and Config Contract

Do not commit credentials. Secrets are provisioned externally (for example by Ansible in the central GitOps repository).

Required Secrets:

- skoda-credentials
  - SKODA_USER
  - SKODA_PASS
  - optional: SKODA_AUTH, SKODA_EVENTS, SKODA_VEHICLE
- mariadb-credentials
  - MARIADB_USERNAME
  - MARIADB_PASSWORD
- graylog-credentials
  - GRAYLOG_HOST

Required ConfigMaps:

- mariadb-app-config
  - MARIADB_DATABASE
  - MARIADB_HOSTNAME
  - MARIADB_PORT
- graylog-app-config
  - GRAYLOG_PORT

## Services Included

Deployments and ClusterIP Services:

- skodaimporter
- skodachargefinder
- skodachargecollector
- skodaupdatechargeprices
- skodachargefrontend

All Deployments include:

- readiness and liveness probes
- non-root runtime defaults where possible
- environment-specific replicas/resources from overlays

## CI Validation

GitHub Actions validates both overlays:

- kustomize build k8s/overlays/prod
- kustomize build k8s/overlays/dev

The workflow fails if either build fails.
