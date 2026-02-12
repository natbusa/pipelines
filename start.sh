#!/usr/bin/env bash
PORT="${PORT:-9099}"
HOST="${HOST:-0.0.0.0}"
PIPELINES_DIR="${PIPELINES_DIR:-./pipelines}"
UVICORN_LOOP="${UVICORN_LOOP:-auto}"

if [[ "${INSTALL_REQUIREMENTS:-false}" == "true" ]]; then
  find -L "$PIPELINES_DIR" -name requirements.txt | while read -r req; do
    echo "Installing requirements from $req"
    pip install -r "$req"
  done
else
  echo "INSTALL_REQUIREMENTS=false, skipping pipeline requirements install"
fi

exec uvicorn main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips '*' --loop "$UVICORN_LOOP" "$@"
