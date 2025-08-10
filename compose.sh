#!/bin/sh

if [ $1 = "up" ]; then
source .venv/bin/activate
folders="skodaimporter skodachargefinder skodachargecollector skodaupdatechargeprices skodachargefrontend"
echo ${folders} | xargs -P 8 -t -n 1 -I {} sh -c 'pip-compile --output-file={}/requirements.txt {}/requirements.in'
echo compiled requirements
docker compose build
fi
docker compose $1
