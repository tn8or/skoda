# Skoda Data Logger

Python microservices for collecting, processing, and visualizing charging data from Skoda EVs via MySkoda.

## Architecture

This repository contains five FastAPI services:

- `skodaimporter`: pulls vehicle data/events from MySkoda and writes raw logs
- `skodachargefinder`: detects charging sessions from raw logs
- `skodachargecollector`: aggregates charge events/hours
- `skodaupdatechargeprices`: enriches charge hours with pricing
- `skodachargefrontend`: web dashboard and health endpoints

Shared utilities live in `commons.py` and per-service `commons.py` / `mariadb.py`.
Database schema is in `sqldump/sqldump.sql`.

## Requirements

- Python 3.13
- Docker + Docker Compose
- MariaDB (via compose or external)
- MySkoda credentials (`SKODA_USER`, `SKODA_PASS`)

## Quick Start

1. Create a virtual environment

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

1. Install development tooling

```bash
pip install pytest pytest-asyncio pytest-cov pytest-mock pip-tools
```

1. Install dependencies per service (no root `requirements.txt`)

```bash
pip install -r skodaimporter/requirements.txt
pip install -r skodachargefinder/requirements.txt
pip install -r skodachargecollector/requirements.txt
pip install -r skodaupdatechargeprices/requirements.txt
pip install -r skodachargefrontend/requirements.txt
```

1. Provide secrets in `secrets/`

Required files:

- `SKODA_USER`
- `SKODA_PASS`
- `MARIADB_DATABASE`
- `MARIADB_USERNAME`
- `MARIADB_PASSWORD`
- `MARIADB_HOSTNAME`
- `GRAYLOG_HOST`
- `GRAYLOG_PORT`
- `env`

## Run

Full local build + test + startup:

```bash
./compose.sh up -d
```

Start only database:

```bash
docker compose up -d mariadb
```

View logs:

```bash
docker compose logs -f skodaimporter
```

Application logs are stdout/stderr based. Use `docker logs` / `kubectl logs`.

## Test

Run all tests:

```bash
source .venv/bin/activate
pytest -q
```

Run one service tests:

```bash
cd skodachargefrontend && pytest -v
```

## Service Endpoints

Default local ports:

- `skodaimporter`: 80
- `skodachargefinder`: 2080
- `skodachargecollector`: 3080
- `skodaupdatechargeprices`: 3081
- `skodachargefrontend`: 3082

## Repository Layout

```text
.
├── commons.py
├── mariadb.py
├── compose.sh
├── docker-compose.yml
├── sqldump/
├── secrets/
├── skodaimporter/
├── skodachargefinder/
├── skodachargecollector/
├── skodaupdatechargeprices/
└── skodachargefrontend/
```

## CI/CD

GitHub Actions:

- `.github/workflows/ci-cd.yml`: tests, security scan, Docker image build/push, deploy webhooks
- `.github/workflows/update-deps.yml`: dependency update automation

## Security Notes

- Never commit secrets
- Keep credentials in `secrets/` (git-ignored)
- Escape UI-rendered values in frontend (`escape_html()`)

## License

MIT (see `LICENSE`)
