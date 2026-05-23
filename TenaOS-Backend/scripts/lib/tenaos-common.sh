#!/bin/bash

TENAOS_SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TENAOS_ROOT_DIR="$(cd -- "$TENAOS_SCRIPT_DIR/../.." && pwd)"
TENAOS_CONTAINER_NAME="${TENAOS_CONTAINER_NAME:-TenaOS-Backend}"

tenaos_log() { echo "[tenaos] $*"; }
tenaos_fail() { echo "[tenaos] ERROR: $*" >&2; exit 1; }

tenaos_default_env() {
  local var_name="$1"
  local default_value="$2"
  if [ -z "${!var_name:-}" ]; then
    export "$var_name=$default_value"
  fi
}

tenaos_apply_defaults() {
  tenaos_default_env OPENMRS_PORT "18080"
  tenaos_default_env OPENMRS_PUBLIC_SCHEME "http"
  tenaos_default_env TENAOS_PUBLIC_HOST "localhost"
  tenaos_default_env OPENMRS_DB_NAME "openmrs"
  tenaos_default_env OPENMRS_DB_USER "openmrs"
  tenaos_default_env OPENMRS_JAVA_MEMORY_OPTS "-Xmx1g"
  tenaos_default_env OPENMRS_KEYCLOAK_ENABLED "false"
}

tenaos_compute_urls() {
  tenaos_default_env OPENMRS_PUBLIC_URL "${OPENMRS_PUBLIC_SCHEME}://${TENAOS_PUBLIC_HOST}:${OPENMRS_PORT}/openmrs"
}

tenaos_require_env() {
  local var_name
  for var_name in "$@"; do
    if [ -z "${!var_name:-}" ]; then
      tenaos_fail "Required environment variable '$var_name' is not set."
    fi
  done
}

tenaos_require_runtime_secrets() {
  tenaos_require_env OPENMRS_DB_PASSWORD OPENMRS_ADMIN_PASSWORD
}

tenaos_load_host_env_files() {
  local env_file
  for env_file in "$TENAOS_ROOT_DIR/.env"; do
    if [ -f "$env_file" ]; then
      set -a
      # shellcheck disable=SC1090
      . "$env_file"
      set +a
    fi
  done
}

tenaos_load_env() {
  tenaos_load_host_env_files
  tenaos_apply_defaults
  tenaos_compute_urls
}

tenaos_docker_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return
  fi
  tenaos_fail "Docker Compose plugin or docker-compose is required."
}
