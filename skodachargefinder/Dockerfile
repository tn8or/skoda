FROM python:3.13-alpine AS build

WORKDIR /app
ARG TARGETPLATFORM
RUN echo "I'm building for $TARGETPLATFORM"

# Install system dependencies required for MariaDB and compilation
RUN apk add --no-cache mariadb-client mariadb-connector-c-dev gcc musl-dev

# Set up Python virtual environment
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Upgrade pip and install build tools
RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade pip pip-tools

# Install production dependencies
COPY ./requirements.txt /tmp
RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade -r /tmp/requirements.txt

# Test stage - MANDATORY - runs tests before building final image
FROM build AS test

RUN echo "=== RUNNING MANDATORY TEST STAGE ==="

# Install test dependencies
RUN . /opt/venv/bin/activate && pip install --no-cache-dir \
    pytest>=7.0.0 \
    pytest-asyncio>=0.21.0 \
    pytest-mock>=3.10.0 \
    pytest-cov>=4.0.0

# Copy source code and tests (ensure tests directory exists)
COPY . /app
WORKDIR /app

# Verify test files exist before running
RUN if [ ! -d "tests" ]; then \
    echo "ERROR: tests directory not found!" && exit 1; \
    fi

# Run tests with strict coverage requirements
RUN echo "Running tests with coverage requirements..." && \
    . /opt/venv/bin/activate && \
    python -m pytest tests/ \
    -v \
    --tb=short \
    --cov=chargefinder \
    --cov-report=term-missing \
    --cov-report=html:/tmp/coverage \
    --cov-fail-under=35 \
    --strict-markers \
    --disable-warnings

RUN echo "=== ALL TESTS PASSED SUCCESSFULLY ==="

# Final production stage - only built if tests pass
FROM python:3.13-alpine AS final

# Set up runtime environment
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /app

# Install only runtime dependencies (no build tools needed)
RUN apk add --no-cache mariadb-connector-c curl

# Copy application code and virtual environment from build stage
COPY --from=test /app /app
COPY --from=build /opt/venv /opt/venv

# Health check to ensure service is running correctly
HEALTHCHECK --interval=60s --timeout=3s --retries=1 --start-period=10s --start-interval=5s \
    CMD curl --fail http://localhost:80 || exit 1

# Start the application
ENTRYPOINT ["uvicorn", "chargefinder:app", "--host", "0.0.0.0", "--port", "80"]
