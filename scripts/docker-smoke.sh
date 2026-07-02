#!/usr/bin/env bash
# Docker release smoke checks for TenaOS.
#
# Default mode is CI-safe: validate docker-compose rendering and build the
# frontend image stage. Full container boot requires the large model/CIEL/Qdrant
# artifacts and is intentionally opt-in.

set -euo pipefail

cd "$(dirname "$0")/.."

mode="${1:-build}"
image_tag="${TENAOS_DOCKER_SMOKE_IMAGE:-tenaos:frontend-smoke}"

export TENAOS_PROFILE="${TENAOS_PROFILE:-demo}"
export OPENMRS_DB_PASSWORD="${OPENMRS_DB_PASSWORD:-Admin123}"
export OPENMRS_ADMIN_PASSWORD="${OPENMRS_ADMIN_PASSWORD:-Admin123}"
export TENAOS_EMBED_MODEL_PATH="${TENAOS_EMBED_MODEL_PATH:-/tmp/tenaos-smoke/embedgemma-300m}"
export TENAOS_CIEL_SQLITE_PATH="${TENAOS_CIEL_SQLITE_PATH:-/tmp/tenaos-smoke/ciel_search.sqlite3}"
export TENAOS_QDRANT_SNAPSHOTS_PATH="${TENAOS_QDRANT_SNAPSHOTS_PATH:-/tmp/tenaos-smoke/qdrant-snapshots}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: required command not found: $1" >&2
    exit 127
  fi
}

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
    return
  fi
  echo "ERROR: neither 'docker compose' nor 'docker-compose' is available" >&2
  exit 127
}

compose_config_check() {
  require_command docker
  compose config -q
  echo "docker compose config ok"
}

frontend_stage_build() {
  require_command docker
  docker build --target frontend-build --tag "$image_tag" .
  echo "docker frontend-build stage ok: $image_tag"
}

full_boot_smoke() {
  require_command docker
  for path in "$TENAOS_EMBED_MODEL_PATH" "$TENAOS_QDRANT_SNAPSHOTS_PATH"; do
    if [ ! -d "$path" ]; then
      echo "ERROR: full smoke requires existing directory: $path" >&2
      exit 2
    fi
  done
  if [ ! -f "$TENAOS_CIEL_SQLITE_PATH" ]; then
    echo "ERROR: full smoke requires existing CIEL sqlite file: $TENAOS_CIEL_SQLITE_PATH" >&2
    exit 2
  fi

  if compose up --help 2>&1 | grep -q -- "--wait"; then
    compose up --build --detach --wait
  else
    compose up --build --detach
    local container="${TENAOS_CONTAINER_NAME:-TenaOS_v1}"
    local deadline=$((SECONDS + 900))
    while [ "$SECONDS" -lt "$deadline" ]; do
      local health
      health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
      if [ "$health" = "healthy" ]; then
        break
      fi
      if [ "$health" = "unhealthy" ] || [ "$health" = "exited" ] || [ "$health" = "dead" ]; then
        docker logs "$container" --tail 200 >&2 || true
        echo "ERROR: container reached terminal state: $health" >&2
        exit 1
      fi
      sleep 10
    done
    if [ "$SECONDS" -ge "$deadline" ]; then
      docker logs "$container" --tail 200 >&2 || true
      echo "ERROR: timed out waiting for healthy container: $container" >&2
      exit 1
    fi
  fi
  curl -fsS "http://127.0.0.1:${TENAOS_HOST_PORT:-8080}/agent-api/health" >/dev/null
  curl -fsS "http://127.0.0.1:${TENAOS_HOST_PORT:-8080}/" >/dev/null
  echo "docker compose full boot smoke ok"
}

case "$mode" in
  config)
    compose_config_check
    ;;
  build)
    compose_config_check
    frontend_stage_build
    ;;
  full)
    compose_config_check
    full_boot_smoke
    ;;
  *)
    echo "Usage: $0 [config|build|full]" >&2
    exit 64
    ;;
esac
