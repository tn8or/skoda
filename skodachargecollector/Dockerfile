FROM python:3.13-alpine AS build

WORKDIR /app
ARG TARGETPLATFORM
RUN echo "I'm building for $TARGETPLATFORM"
RUN apk add --no-cache gcc musl-dev

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade pip pip-tools

COPY ./requirements.txt /tmp

RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade -r /tmp/requirements.txt

# Include application source for downstream stages
COPY . /app

# Test stage - MANDATORY - runs tests before building final image
FROM build AS test

RUN echo "=== RUNNING MANDATORY TEST STAGE ==="

# Install test dependencies
RUN . /opt/venv/bin/activate && pip install --no-cache-dir \
    pytest>=7.0.0 \
    pytest-asyncio>=0.21.0 \
    pytest-mock>=3.10.0 \
    pytest-cov>=4.0.0

# Source already present from build stage
WORKDIR /app

# Verify test files exist before running
RUN if [ ! -d "tests" ]; then \
    echo "ERROR: tests directory not found!" && exit 1; \
    fi

# Run tests with memory-optimized settings and environment variables
RUN echo "Running tests with coverage requirements..." && \
    . /opt/venv/bin/activate && \
    env=test \
    GRAYLOG_HOST=localhost \
    GRAYLOG_PORT=12201 \
    MARIADB_USERNAME=test \
    MARIADB_PASSWORD=test \
    MARIADB_HOSTNAME=localhost \
    MARIADB_DATABASE=test \
    python -m pytest tests/ \
    -v \
    --tb=short \
    --cov=chargecollector \
    --cov-report=term-missing \
    --cov-fail-under=35 \
    --strict-markers \
    --disable-warnings \
    --maxfail=3 \
    -x

RUN echo "=== ALL TESTS PASSED SUCCESSFULLY ==="

# Final production stage - only built if tests pass
FROM python:3.13-alpine AS final

# Set up runtime environment
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /app

# Install only runtime dependencies (no build tools needed)
RUN apk add --no-cache curl

# Copy application code and virtual environment from build stage only
COPY --from=build /app /app
COPY --from=build /opt/venv /opt/venv

# Health check to ensure service is running correctly
HEALTHCHECK --interval=60s --timeout=3s --retries=1 --start-period=10s --start-interval=5s \
    CMD curl --fail http://localhost:80 || exit 1

# Start the application
ENTRYPOINT ["uvicorn", "chargecollector:app", "--host", "0.0.0.0", "--port", "80"]
