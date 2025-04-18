FROM python:3.13-alpine AS build

WORKDIR /app
VOLUME [ "/data" ]
ARG TARGETPLATFORM
RUN echo "I'm building for $TARGETPLATFORM"
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade pip

COPY ./requirements.txt /app

RUN . /opt/venv/bin/activate && pip install --no-cache-dir --upgrade -r ./requirements.txt

COPY . /app

ENTRYPOINT  ["uvicorn","main:app","--host","0.0.0.0","--port","80"]
