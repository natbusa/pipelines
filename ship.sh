#!/usr/bin/env bash
set -euo pipefail

IMAGE="natbusa/open-webui-pipelines"
TAG="${1:-latest}"
BUILD_HASH=$(git rev-parse --short HEAD)

echo "Building ${IMAGE}:${TAG} (hash=${BUILD_HASH})"
docker build \
  --build-arg BUILD_HASH="${BUILD_HASH}" \
  -t "${IMAGE}:${TAG}" \
  .

echo "Pushing ${IMAGE}:${TAG}"
docker push "${IMAGE}:${TAG}"

echo "Done: ${IMAGE}:${TAG} (${BUILD_HASH})"
