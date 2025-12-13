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
- **Full build with testing**: `./compose.sh up` - Takes 30-60 minutes including mandatory Docker test stages.
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

## Validation

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
./compose.sh up

# Start only database for testing (WORKS - takes ~5 seconds)
docker compose up -d mariadb

# View service logs
docker compose logs -f skodaimporter

# Access database (VERIFIED working command)
docker exec mariadb mariadb -uskoda -pskodapass skoda -e "SHOW TABLES;"

# Update a single service's dependencies
pip-compile --upgrade --output-file=skodaimporter/requirements.txt skodaimporter/requirements.in
```

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