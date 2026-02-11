#!/usr/bin/env bash
PORT="${PORT:-9099}"
HOST="${HOST:-0.0.0.0}"
PIPELINES_DIR=${PIPELINES_DIR:-./pipelines}
UVICORN_LOOP="${UVICORN_LOOP:-auto}"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

reset_pipelines_dir() {
  if [ -d "$PIPELINES_DIR" ]; then
    rm -rf "${PIPELINES_DIR:?}"/*
    mkdir -p "$PIPELINES_DIR"
    echo "Reset pipelines directory: $PIPELINES_DIR"
  fi
}

install_requirements() {
  if [[ -f "$1" ]]; then
    echo "Installing requirements from $1"
    pip install -r "$1"
  fi
}

download_pipelines() {
  local path=$1
  local destination=$2

  # Remove any surrounding quotes from the path
  path=$(echo "$path" | sed 's/^"//;s/"$//')

  echo "Downloading pipeline from $path"

  if [[ "$path" =~ ^https://github.com/.*/.*/blob/.* ]]; then
    dest_file=$(basename "$path")
    curl -L "$path?raw=true" -o "$destination/$dest_file"
  elif [[ "$path" =~ ^https://github.com/.*/.*/tree/.* ]]; then
    git_repo=$(echo "$path" | awk -F '/tree/' '{print $1}')
    subdir=$(echo "$path" | awk -F '/tree/' '{print $2}')
    git clone --depth 1 --filter=blob:none --sparse "$git_repo" "$destination"
    (
      cd "$destination" || exit
      git sparse-checkout set "$subdir"
    )
  elif [[ "$path" =~ \.py$ ]]; then
    dest_file=$(basename "$path")
    curl -L "$path" -o "$destination/$dest_file"
  else
    echo "Invalid URL format: $path"
    exit 1
  fi
}

install_frontmatter_requirements() {
  local file=$1
  local file_content=$(cat "$1")
  local first_block=$(echo "$file_content" | awk '/"""/{flag=!flag; if(flag) count++; if(count == 2) {exit}} flag' )
  local requirements=$(echo "$first_block" | grep -i 'requirements:')

  if [ -n "$requirements" ]; then
    requirements=$(echo "$requirements" | awk -F': ' '{print $2}' | tr ',' ' ' | tr -d '\r')
    echo "Installing frontmatter requirements: $requirements"
    pip install $requirements
  fi
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Setup phase: download pipelines, install deps
# ---------------------------------------------------------------------------

if [[ "$MODE" == "setup" || "$MODE" == "full" ]]; then
  [[ "$RESET_PIPELINES_DIR" == "true" ]] && reset_pipelines_dir

  [[ -n "$PIPELINES_REQUIREMENTS_PATH" ]] && install_requirements "$PIPELINES_REQUIREMENTS_PATH"

  if [[ -n "$PIPELINES_URLS" ]]; then
    mkdir -p "$PIPELINES_DIR"

    IFS=';' read -ra ADDR <<< "$PIPELINES_URLS"
    for path in "${ADDR[@]}"; do
      download_pipelines "$path" "$PIPELINES_DIR"
    done

    if [ "${INSTALL_FRONTMATTER_REQUIREMENTS:-false}" = "true" ]; then
      for file in "$PIPELINES_DIR"/*; do
        [[ -f "$file" ]] && install_frontmatter_requirements "$file"
      done

      for dir in "$PIPELINES_DIR"/*/; do
        init_file="${dir}__init__.py"
        [[ -f "$init_file" ]] && install_frontmatter_requirements "$init_file"
      done
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Run phase: start uvicorn
# ---------------------------------------------------------------------------

if [[ "$MODE" == "run" || "$MODE" == "full" ]]; then
  uvicorn main:app --host "$HOST" --port "$PORT" --forwarded-allow-ips '*' --loop "$UVICORN_LOOP" $RELOAD
fi
