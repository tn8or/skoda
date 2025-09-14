FROM python:3.13-alpine AS build

WORKDIR /app
ARG TARGETPLATFORM
RUN echo "I'm building for $TARGETPLATFORM"
RUN apk add --no-cache gcc musl-dev

ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Configure PyPI proxy
RUN mkdir -p /root/.config/pip && \
    echo "[global]" > /root/.config/pip/pip.conf && \
    echo "index-url = http://my-pypi-pypi-server.pypi.svc.cluster.local/simple/" >> /root/.config/pip/pip.conf && \
    echo "trusted-host = my-pypi-pypi-server.pypi.svc.cluster.local" >> /root/.config/pip/pip.conf

RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade pip pip-tools

COPY ./requirements.txt /tmp

RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade -r /tmp/requirements.txt

FROM python:3.13-alpine AS final

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /app
RUN apk add --no-cache curl

COPY . /app
COPY --from=build /opt /opt

ENTRYPOINT  ["uvicorn","skodaupdatechargeprices:app","--host","0.0.0.0","--port","80"]
HEALTHCHECK --interval=60s --timeout=3s --retries=1 --start-period=10s --start-interval=5s CMD curl --fail http://localhost:80 || exit 1
