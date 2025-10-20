# Use build arg to support multiple registries for reliability
ARG BASE_REGISTRY=""
ARG PYTHON_IMAGE=${BASE_REGISTRY}python:3.13-alpine
FROM ${PYTHON_IMAGE} AS build

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

FROM ${PYTHON_IMAGE} AS final

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /app
RUN apk add --no-cache curl

COPY . /app
COPY --from=build /opt /opt

ENTRYPOINT  ["uvicorn","chargeimporter:app","--host","0.0.0.0","--port","80","--timeout-graceful-shutdown","10"]
HEALTHCHECK --interval=60s --timeout=3s --retries=1 --start-period=10s --start-interval=5s CMD curl --fail http://localhost:80 || exit 1
