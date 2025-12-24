# Skoda Data Logger — Copilot helper (concise)

Purpose: short, actionable notes for AI coding agents to be productive immediately.

1) Big picture
- Five FastAPI microservices in top-level folders: `skodaimporter`, `skodachargefinder`, `skodachargecollector`, `skodaupdatechargeprices`, `skodachargefrontend`.
- Shared utilities: top-level `commons.py`, per-service `commons.py`/`mariadb.py`. DB schema: `sqldump/sqldump.sql`.
- Data flow: ingest rawlogs → detect charge events (chargefinder) → persist/aggregate (chargecollector) → prices (updatechargeprices) → UI (frontend).

2) Environment & quick setup
- Python 3.13 is required (myskoda client). Create env: `python3.13 -m venv .venv && source .venv/bin/activate`.
- Install service deps individually: `pip install -r SERVICE/requirements.txt`.
- Dev tools: `pip install pytest pytest-asyncio pytest-cov pytest-mock pip-tools`.
- Secrets: `./secrets/` holds SKODA_USER, SKODA_PASS, MARIADB_*, GRAYLOG_HOST/PORT, env. Use `commons.load_secret()` to read them.

3) Build, test, run (practical)
- Full pipeline (includes tests + image build): `./compose.sh up -d` — long; do not cancel.
- Quick run tests: `source .venv/bin/activate && pytest -q` (or per-service `cd SERVICE && pytest -v`).
- Coverage gates: `skodachargefinder >=50%`, `skodachargecollector >=85%`, `skodachargefrontend >=70%`. Docker builds enforce test stages.
- Fast DB-only check: `docker compose up -d mariadb`; verify: `docker exec mariadb mariadb -uskoda -pskodapass skoda -e "SHOW TABLES;"`.

4) Project-specific patterns to follow
- Defensive imports: optional drivers and `myskoda` are often lazily imported with try/except; preserve that style for optional dependencies.
- Use `commons.pull_api()` and `commons.load_secret()` rather than ad-hoc HTTP/secret code.
- Health logic: `skodaimporter/chargeimporter.py` contains enhanced health checks (event timeout, API health, MQTT state). When updating health behavior, follow its graduated response pattern.
- XSS rule: always escape user/database values rendered into HTML using `escape_html()` in `skodachargefrontend` (see `XSS_FIX_SUMMARY.md` and `tests/test_xss_prevention.py`).

5) Integration & notable failure modes
- MySkoda client (`myskoda`) can raise `AuthorizationFailedError` or `MarketingConsentError` during auth flows; code already handles both in `skodaimporter/chargeimporter.py`.
- MQTT streaming state is exposed on `myskoda.mqtt`; health checks may attempt reconnects — prefer reusing existing `attempt_mqtt_reconnect()` helper if present.
- DB: `mariadb` driver may be missing in CI; code handles missing driver gracefully — avoid assuming DB availability in unit tests.

6) When making changes
- Keep changes minimal and run the affected service's tests. After tests pass, re-run `./compose.sh up -d` to validate Docker build stages.
- Preserve existing public APIs (endpoints) and logging format; use `get_logger()` in `commons.py` for consistent log formatting to Graylog and `app.log`.

7) Key files to inspect quickly
- `commons.py` — secrets, URLs, `pull_api`, `load_secret`, `get_logger()`.
- `skodaimporter/chargeimporter.py` — MySkoda connect, `on_event`, enhanced health checks.
- `skodachargefrontend/skodachargefrontend.py` — templates and `escape_html()` usage.
- `sqldump/sqldump.sql` — database schema for `rawlogs`, `charge_events`, `charge_hours`.

If you want, I can (A) trim this further, (B) add example `pytest` invocations per service, or (C) merge this with the longer README content — which would you prefer? Please tell me what to refine.
# Skoda Data Logger - Microservices Architecture

This repository contains a Python-based microservices system for collecting, processing, and analyzing vehicle charging data from Skoda Enyaq electric vehicles. The system integrates with MySkoda API and stores data in MariaDB with a web frontend for visualization.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Working Effectively

### Prerequisites and Setup
- **CRITICAL**: Requires Python 3.13 (myskoda==2.3.3 dependency requires Python >=3.13)
- **Install Python 3.13**: `sudo apt update && sudo apt install -y software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt update && sudo apt install -y python3.13 python3.13-venv python3.13-dev`
- Docker and docker-compose for containerization
- MariaDB for data storage

### Bootstrap the Development Environment
1. **Create virtual environment**: `python3.13 -m venv .venv`
2. **Activate virtual environment**: `source .venv/bin/activate`
3. **Install development tools**: `pip install pytest pytest-asyncio pytest-cov pytest-mock pip-tools`
4. **Install service dependencies** (choose one service to work on):
   - `pip install -r skodaimporter/requirements.txt` (for importer service)
   - `pip install -r skodachargefinder/requirements.txt` (for charge finder service)
   - `pip install -r skodachargecollector/requirements.txt` (for charge collector service)
   - `pip install -r skodaupdatechargeprices/requirements.txt` (for price update service)
   - `pip install -r skodachargefrontend/requirements.txt` (for frontend service)

### Build and Test Process
- **Full build with testing**: `./compose.sh up -d` - Takes 30-60 minutes including mandatory Docker test stages.
  - Step 1: Activates .venv and runs pytest -q (5-10 minutes)
  - Step 2: Compiles requirements for all services in parallel (2-5 minutes)
  - Step 3: Docker builds all services with mandatory test stages (20-45 minutes)
  - Step 4: Starts services with docker-compose
- **Run tests only**: `source .venv/bin/activate && pytest -q` - Takes 5-15 minutes.
  - **MEASURED**: Frontend service: 20 tests pass in 0.28s with 74% coverage
  - **REQUIRES**: Service dependencies must be installed locally (pip install -r SERVICE/requirements.txt)
- **Update dependencies**: `pip-compile --upgrade --output-file=SERVICE/requirements.txt SERVICE/requirements.in` for each SERVICE
- **Docker build single service**: `docker build -t SERVICE ./SERVICE` - Takes 15-30 minutes per service due to mandatory test stages.
- **MariaDB only**: `docker compose up -d mariadb` - Takes ~5 seconds to pull and start
  - Database accessible on port 3306 with credentials: skoda/skodapass

### Development Workflow
- **Start development environment**: `docker-compose up -d` (after building)
- **View logs**: `docker-compose logs -f SERVICE_NAME`
- **Stop services**: `docker-compose down`

## Local Validation Policy
- Before marking any task complete, always:
  1) Run pytest from the repo root (or the affected service) inside the Python 3.13 venv.
  2) Rebuild and redeploy containers with `./compose.sh up -d` (do not cancel; expected <1 minute here).
- Only skip either step if the user explicitly says to skip.

### Mandatory Test Requirements
- **ALWAYS run tests before committing**: Each service has mandatory coverage requirements
  - skodachargefinder: 50% coverage minimum (`--cov-fail-under=50`)
  - skodachargecollector: 85% coverage minimum (`--cov-fail-under=85`)
  - skodachargefrontend: 70% coverage minimum (`--cov-fail-under=70`)
  - skodaimporter: Basic pytest configuration (no coverage requirement)
  - skodaupdatechargeprices: Basic pytest configuration (no coverage requirement)
- **Docker builds include mandatory test stages**: Tests MUST pass before final image is built
- **Test command per service**: `cd SERVICE && pytest -v` (uses pytest.ini configuration)
- **Test all services**: `source .venv/bin/activate && pytest -q` (from repository root)
  - **REQUIRES**: All service dependencies must be installed locally
  - **MEASURED timing**: Frontend service completes in 0.28s (20 tests, 74% coverage)

### End-to-End Validation Scenarios
After making changes, ALWAYS test these complete scenarios:

1. **Service Health Checks**:
   - `curl http://localhost:PORT/` for each service to verify it responds
   - Check MariaDB connectivity: `docker exec mariadb mariadb -uskoda -pskodapass skoda -e "SHOW TABLES;"`
   - **Expected tables**: charge_events, charge_hours, rawlogs

2. **Data Flow Validation**:
   - Import data: Verify skodaimporter can connect to MySkoda API (requires SKODA_USER/SKODA_PASS secrets)
   - Process charges: Verify chargefinder identifies charging events
   - Collect data: Verify chargecollector processes charge information
   - Update prices: Verify price updates work correctly
   - View frontend: Access web interface at configured port

3. **Database Validation**:
   - Start MariaDB: `docker compose up -d mariadb` (takes ~5 seconds)
   - Verify connection: `docker exec mariadb mariadb -uskoda -pskodapass skoda -e "SHOW TABLES;"`
   - Check data flow: Raw logs → processed events → aggregated charge hours

## Common Tasks

### Service Ports and URLs
- skodaimporter: Port 80 (main data importer)
- skodachargefinder: Port 2080 (charge event detection)
- skodachargecollector: Port 3080 (charge data collection)
- skodaupdatechargeprices: Port 3081 (price updates)
- skodachargefrontend: Port 3082 (web interface)
- MariaDB: Port 3306

### Configuration and Secrets
- **Secrets directory**: `./secrets/` (git-ignored, must be created manually)
- **Required secrets**:
  - SKODA_USER, SKODA_PASS (MySkoda API credentials)
  - MARIADB_DATABASE, MARIADB_USERNAME, MARIADB_PASSWORD, MARIADB_HOSTNAME (database config)
  - GRAYLOG_HOST, GRAYLOG_PORT (logging config)
  - env (environment: prod/dev)
- **Create test secrets**:
  ```bash
  mkdir -p secrets
  echo "testuser" > secrets/SKODA_USER
  echo "testpass" > secrets/SKODA_PASS
  echo "skoda" > secrets/MARIADB_DATABASE
  echo "skoda" > secrets/MARIADB_USERNAME
  echo "skodapass" > secrets/MARIADB_PASSWORD
  echo "mariadb" > secrets/MARIADB_HOSTNAME
  echo "prod" > secrets/env
  echo "localhost" > secrets/GRAYLOG_HOST
  echo "12201" > secrets/GRAYLOG_PORT
  ```
- **Database schema**: Located in `sqldump/sqldump.sql`
- **Docker secrets**: Mounted via docker-compose.yml secrets configuration

### Project Structure
```
.
├── skodaimporter/           # Main MySkoda API data importer service
├── skodachargefinder/       # Charging event detection service
├── skodachargecollector/    # Charge data collection and processing
├── skodaupdatechargeprices/ # Pricing information updates
├── skodachargefrontend/     # Web frontend for data visualization
├── commons.py               # Shared utilities across services
├── mariadb.py              # Database connection utilities
├── compose.sh              # Build orchestration script
├── docker-compose.yml      # Service orchestration
├── sqldump/                # Database schema and initialization
└── .github/workflows/      # CI/CD pipelines
```

### Frequently Used Commands
```bash
# Activate development environment
source .venv/bin/activate

# Run all tests with coverage (REQUIRES local dependencies installed)
pytest -q --cov=. --cov-report=term-missing

# Run tests for specific service with exact coverage requirements
cd skodachargefrontend && pytest -v  # 70% coverage required
cd skodachargefinder && pytest -v    # 50% coverage required
cd skodachargecollector && pytest -v # 85% coverage required

# Build and start all services (NEVER CANCEL - 60-90 minutes, FAILS with network issues)
./compose.sh up -d

# Start only database for testing (WORKS - takes ~5 seconds)
docker compose up -d mariadb

# View service logs
docker compose logs -f skodaimporter

# Access database (VERIFIED working command)
docker exec mariadb mariadb -uskoda -pskodapass skoda -e "SHOW TABLES;"

# Update a single service's dependencies
pip-compile --upgrade --output-file=skodaimporter/requirements.txt skodaimporter/requirements.in
```

### Service-Specific Examples
- skodaimporter:
  - Install deps: `pip install -r skodaimporter/requirements.txt`
  - Run tests: `cd skodaimporter && pytest -v`
  - Health check locally: `curl http://localhost:80/`
  - Notes: handles `AuthorizationFailedError` and `MarketingConsentError`; MQTT reconnect via `attempt_mqtt_reconnect()`.
- skodachargefinder:
  - Install deps: `pip install -r skodachargefinder/requirements.txt`
  - Run tests (≥50% cov): `cd skodachargefinder && pytest -v`
  - Trigger detection API from importer: uses `commons.CHARGEFINDER_URL`.
- skodachargecollector:
  - Install deps: `pip install -r skodachargecollector/requirements.txt`
  - Run tests (≥85% cov): `cd skodachargecollector && pytest -v`
  - DB writes: persists charge events → hours; verify tables exist via MariaDB commands above.
- skodaupdatechargeprices:
  - Install deps: `pip install -r skodaupdatechargeprices/requirements.txt`
  - Run tests: `cd skodaupdatechargeprices && pytest -v`
  - Endpoints: `UPDATECHARGES_URL`, `UPDATEALLCHARGES_URL` in `commons.py`.
- skodachargefrontend:
  - Install deps: `pip install -r skodachargefrontend/requirements.txt`
  - Run tests (≥70% cov): `cd skodachargefrontend && pytest -v`
  - Health endpoint: `GET /` returns dashboard + connection checks; enforce `escape_html()` for any templated content.

## Service Dependencies and Architecture

### Core Dependencies
- **FastAPI**: Web framework for all services
- **myskoda==2.3.3**: MySkoda API client (requires Python 3.13)
- **mariadb/pymysql**: Database connectivity
- **httpx**: HTTP client for inter-service communication
- **graypy**: Graylog integration for centralized logging
- **uvicorn**: ASGI server

### Service Communication
- Services communicate via HTTP REST APIs
- URLs defined in commons.py: CHARGEFINDER_URL, CHARGECOLLECTOR_URL, etc.
- Health checks and status endpoints on all services

### Database Schema
- **rawlogs**: Raw log messages from MySkoda API
- **charge_events**: Individual charging start/stop events
- **charge_hours**: Aggregated charging session data with pricing

## CI/CD and GitHub Actions

### Workflow Files
- **ci-cd.yml**: Combined CI/CD pipeline that includes:
  - Testing all services with Python 3.13
  - Security scanning with pip-audit
  - Building and pushing Docker images to GitHub Container Registry
  - Deployment webhooks for test and production environments
- **update-deps.yml**: Automated dependency updates using pip-compile

### Build Pipeline
1. Tests run in parallel for all services
2. Security audit runs with pip-audit for each service
3. Docker images built only after successful tests
4. Multi-platform builds (linux/amd64, linux/arm64)
5. Test images built for PRs and non-main branches
6. Production images built and deployed only from main branch
7. Automatic deployment webhooks after successful builds

### Security
Refer to [SECURITY.md](../SECURITY.md) for details on:
- GitHub Actions security model
- Workflow isolation and privilege separation
- Branch protection and security controls

## Troubleshooting

### Common Issues
- **Python version**: Ensure Python 3.13 is used (myskoda requirement)
- **Test failures**: Check coverage requirements for each service (varies from 50% to 85%)
- **Database connectivity**: Verify MariaDB secrets are properly configured, use `docker exec mariadb mariadb -uskoda -pskodapass skoda -e "SHOW TABLES;"`
- **Missing secrets**: Ensure all required secret files exist in ./secrets/ (create manually with test values)

### Performance Notes
- **NEVER CANCEL builds or tests**: Docker builds include mandatory test stages
- **Memory usage**: Each service test stage requires adequate memory allocation
- **Parallel execution**: compose.sh uses parallel execution for dependency compilation
- **MEASURED timings**:
  - Frontend tests: 0.28s for 20 tests with 74% coverage
  - Development tools installation: ~30 seconds
  - Dependency compilation: ~6 seconds per service
  - MariaDB startup: ~5 seconds
  - Docker service builds: 15-30 minutes per service

Always verify that any changes maintain the mandatory test coverage requirements and that all services can communicate properly with the database and each other.

## Python Coding Standards

Follow these coding standards when working in this repository:

- Write clear and concise comments for each function
- Ensure functions have descriptive names and include type hints
- Provide docstrings following PEP 257 conventions
- Use the `typing` module for type annotations (e.g., `List[str]`, `Dict[str, int]`)
- Break down complex functions into smaller, more manageable functions
- Follow the **PEP 8** style guide for Python
- Maintain proper indentation (use 4 spaces for each level of indentation)
- Ensure lines do not exceed 79 characters
- Always include test cases for critical paths of the application
- Account for common edge cases like empty inputs, invalid data types, and large datasets

## Security Considerations

- **XSS Prevention**: Always use `escape_html()` function for user input and database content before inserting into HTML templates
- **Input Validation**: Validate and sanitize all user inputs at API boundaries
- **Secrets Management**: Never commit secrets to the repository; use the `./secrets/` directory (git-ignored)
- **Dependency Security**: Regular security audits run via pip-audit in CI/CD pipeline
- For detailed security information, see:
  - [SECURITY.md](../SECURITY.md) - GitHub Actions security model
  - [SECURITY_SCAN_GUIDE.md](../SECURITY_SCAN_GUIDE.md) - Security scanning procedures
  - [XSS_FIX_SUMMARY.md](../XSS_FIX_SUMMARY.md) - XSS vulnerability fixes

## Additional Documentation

- **[README.md](../README.md)**: Quick start guide and project overview
- **[SECURITY.md](../SECURITY.md)**: Security policies and GitHub Actions security
- **[SECURITY_SCAN_GUIDE.md](../SECURITY_SCAN_GUIDE.md)**: How to run security scans
- **[XSS_FIX_SUMMARY.md](../XSS_FIX_SUMMARY.md)**: Details on XSS vulnerability fixes