#!/bin/sh
set -e

if [ $1 = "up" ]; then
source .venv/bin/activate
pytest -q
folders="skodaimporter skodachargefinder skodachargecollector skodaupdatechargeprices skodachargefrontend"
echo ${folders} | xargs -P 8 -t -n 1 -I {} sh -c 'pip-compile --upgrade --output-file={}/requirements.txt {}/requirements.in'
echo compiled requirements
GIT_COMMIT=$(git rev-parse HEAD || true)
GIT_TAG=$(git describe --tags --always --dirty || true)
BUILD_DATE=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
docker compose build \
	--build-arg GIT_COMMIT="${GIT_COMMIT}" \
	--build-arg GIT_TAG="${GIT_TAG}" \
	--build-arg BUILD_DATE="${BUILD_DATE}"
fi
docker compose $1
