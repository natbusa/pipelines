#!/usr/bin/env bash
PORT="${PORT:-9099}"
HOST="${HOST:-0.0.0.0}"
PIPELINES_DIR="${PIPELINES_DIR:-./pipelines}"
UVICORN_LOOP="${UVICORN_LOOP:-auto}"

install_pipeline_requirements() {
  for req in "$PIPELINES_DIR"/**/requirements.txt; do
    [ -f "$req" ] || continue
    echo "Installing requirements from $req"
    pip install -r "$req"
  done
}

MODE="full"
RELOAD=""

while [[ "$#" -gt 0 ]]; do
  case $1 in
    --mode) MODE="$2"; shift ;;
    --reload) RELOAD="--reload" ;;
    *) echo "Unknown parameter: $1"; exit 1 ;;
  esac
  shift
done

if [[ "$MODE" != "setup" && "$MODE" != "run" && "$MODE" != "full" ]]; then
  echo "Invalid mode: $MODE"
  echo "Usage: ./start.sh [--mode setup|run|full] [--reload]"
  exit 1
fi

if [[ "$MODE" == "setup" || "$MODE" == "full" ]]; then
  install_pipeline_requirements
fi

if [[ "$MODE" == "run" || "$MODE" == "full" ]]; then
  uvicorn main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips '*' --loop "$UVICORN_LOOP" $RELOAD
fi
