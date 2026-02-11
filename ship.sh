#!/usr/bin/env bash
set -euo pipefail

IMAGE="natbusa/open-webui-pipelines:latest"

echo "Building ${IMAGE} ..."
docker build -t "${IMAGE}" .

echo "Pushing ${IMAGE} ..."
docker push "${IMAGE}"

echo "Done."
