#!/bin/sh

if [ $1 = "up" ]; then
source .venv/bin/activate
pip-compile --output-file=skodaimporter/requirements.txt skodaimporter/requirements.in
pip-compile --output-file=skodachargefinder/requirements.txt skodachargefinder/requirements.in
docker compose build
fi
docker compose $1
