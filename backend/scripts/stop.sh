#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."
if [ -n "${COMPOSE:-}" ]; then
  compose_cmd=( $COMPOSE )
elif docker compose version >/dev/null 2>&1; then
  compose_cmd=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose_cmd=(docker-compose)
else
  echo "Docker Compose plugin or docker-compose is required." >&2
  exit 1
fi
"${compose_cmd[@]}" down
