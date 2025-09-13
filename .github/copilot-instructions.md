# Skoda Data Logger - Microservices Architecture

This repository contains a Python-based microservices system for collecting, processing, and analyzing vehicle charging data from Skoda Enyaq electric vehicles. The system integrates with MySkoda API and stores data in MariaDB with a web frontend for visualization.

Always reference these instructions first and fallback to search or bash commands only when you encounter unexpected information that does not match the info here.

## Working Effectively

### Prerequisites and Setup
- **CRITICAL**: Requires Python 3.13 (myskoda==2.3.3 dependency requires Python >=3.13)
- Install Python 3.13: `sudo apt update && sudo apt install -y software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt update && sudo apt install -y python3.13 python3.13-venv python3.13-dev`
- Docker and docker-compose for containerization
- MariaDB for data storage

### Bootstrap the Development Environment
1. **Create virtual environment**: `python3.13 -m venv .venv`
2. **Activate virtual environment**: `source .venv/bin/activate`
3. **Install development tools**: `pip install pytest pytest-asyncio pytest-cov pytest-mock pip-tools`
4. **Install service dependencies** (choose one service to work on):
   - `pip install -r skodaimporter/requirements.txt` (for importer service)
   - `pip install -r skodachargefinder/requirements.txt` (for charge finder service)
   - etc.

**NOTE**: If network connectivity prevents pip installs, you can still work with the codebase using Docker builds exclusively. The Docker builds handle all dependencies internally.

### Build and Test Process
- **Full build with testing**: `./compose.sh up` - NEVER CANCEL: Takes 60-90 minutes including mandatory Docker test stages. Set timeout to 120+ minutes.
  - Step 1: Activates .venv and runs pytest -q (5-10 minutes)
  - Step 2: Compiles requirements for all services in parallel (2-5 minutes)  
  - Step 3: Docker builds all services with mandatory test stages (45-75 minutes)
  - Step 4: Starts services with docker-compose
- **Run tests only**: `source .venv/bin/activate && pytest -q` - Takes 5-15 minutes. NEVER CANCEL. Set timeout to 30+ minutes.
- **Update dependencies**: `pip-compile --upgrade --output-file=SERVICE/requirements.txt SERVICE/requirements.in` for each SERVICE
- **Docker build single service**: `docker build -t SERVICE ./SERVICE` - NEVER CANCEL: Takes 30-45 minutes per service due to mandatory test stages. Set timeout to 60+ minutes.

### Development Workflow
- **Start development environment**: `docker-compose up -d` (after building)
- **View logs**: `docker-compose logs -f SERVICE_NAME`
- **Stop services**: `docker-compose down`

## Validation

### Mandatory Test Requirements
- **ALWAYS run tests before committing**: Each service has mandatory coverage requirements
  - skodachargefinder: 50% coverage minimum (`--cov-fail-under=50`)
  - skodachargecollector: 85% coverage minimum (`--cov-fail-under=85`)
  - Other services: Coverage requirements vary (check individual pytest.ini files)
- **Docker builds include mandatory test stages**: Tests MUST pass before final image is built
- **Test command per service**: `cd SERVICE && pytest -v` (uses pytest.ini configuration)
- **Test all services**: `source .venv/bin/activate && pytest -q` (from repository root)

### End-to-End Validation Scenarios
After making changes, ALWAYS test these complete scenarios:

1. **Service Health Checks**:
   - `curl http://localhost:PORT/` for each service to verify it responds
   - Check MariaDB connectivity: `docker exec -it mariadb mysql -uskoda -pskodapass skoda -e "SHOW TABLES;"`

2. **Data Flow Validation**:
   - Import data: Verify skodaimporter can connect to MySkoda API
   - Process charges: Verify chargefinder identifies charging events
   - Collect data: Verify chargecollector processes charge information
   - Update prices: Verify price updates work correctly
   - View frontend: Access web interface at configured port

3. **Database Validation**:
   - Verify tables exist: `charge_events`, `charge_hours`, `rawlogs`
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
- **Secrets directory**: `./secrets/` (git-ignored)
- **Required secrets**: SKODA_USER, SKODA_PASS, MARIADB_*, GRAYLOG_*, env
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

# Run all tests with coverage
pytest -q --cov=. --cov-report=term-missing

# Build and start all services (NEVER CANCEL - 60-90 minutes)
./compose.sh up

# View service logs
docker-compose logs -f skodaimporter

# Access database
docker exec -it mariadb mysql -uskoda -pskodapass skoda

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
- **ci.yml**: Runs tests on all services with Python 3.13
- **ghcr-image.yml**: Builds and pushes Docker images to GitHub Container Registry
- **pip-audit.yml**: Security scanning of dependencies
- **update-deps.yml**: Automated dependency updates

### Build Pipeline
1. Tests run in parallel for all services
2. Docker images built only after successful tests
3. Multi-platform builds (linux/amd64, linux/arm64)
4. Automatic deployment webhook after successful builds

## Troubleshooting

### Common Issues
- **Python version**: Ensure Python 3.13 is used (myskoda requirement)
- **Network timeouts**: Docker builds can take 30-45 minutes per service
- **PyPI connectivity**: If pip install fails with timeouts, try `pip install --timeout 300 --retries 5` or use Docker builds instead
- **Python 3.13 installation**: If deadsnakes PPA fails, try alternative installation methods or use Docker exclusively
- **Test failures**: Check coverage requirements for each service (varies from 50% to 85%)
- **Database connectivity**: Verify MariaDB secrets are properly configured
- **Missing secrets**: Ensure all required secret files exist in ./secrets/
- **Docker Alpine package issues**: If apk add fails in Docker builds, this indicates network connectivity problems that usually resolve on retry
- **local caching** all runners should utilize local caching in the path /home/cache
- **local runners** ensure that all actions, if possible, are running on self-hosted runners in the skoda-runner-set 

### Performance Notes
- **NEVER CANCEL builds or tests**: Docker builds include mandatory test stages
- **Timeout settings**: Always use 60+ minutes for builds, 30+ minutes for tests
- **Memory usage**: Each service test stage requires adequate memory allocation
- **Parallel execution**: compose.sh uses parallel execution for dependency compilation

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
