#!/bin/sh

if [ $1 = "up" ]; then
source .venv/bin/activate
pip-compile --output-file=skodaimporter/requirements.txt skodaimporter/requirements.in
pip-compile --output-file=skodachargefinder/requirements.txt skodachargefinder/requirements.in
pip-compile --output-file=skodachargecollector/requirements.txt skodachargecollector/requirements.in
pip-compile --output-file=skodaupdatechargeprices/requirements.txt skodaupdatechargeprices/requirements.in
docker compose build
fi
docker compose $1
