#!/bin/sh

if [ $1 = "up" ]; then
source .venv/bin/activate
pip-compile --output-file=requirements.txt requirements.in
docker compose build
fi
docker compose $1
