#!/usr/bin/env bash
# TenaOS one-command local demo setup.
#
# This wrapper keeps the manual path in README.md reproducible while removing
# the fragile copy/paste steps for first-time operators:
#   * validates Docker, GPU visibility, ports, and password policy
#   * fetches host-mounted artifacts
#   * writes .env with the exact artifact paths
#   * launches Docker Compose and waits for a healthy container unless --skip-up is provided
set -euo pipefail

cd "$(dirname "$0")/.."

TARGET_DIR="${TENAOS_BOOTSTRAP_DIR:-$(pwd)/tenaos-bootstrap}"
HOST_PORT="${TENAOS_HOST_PORT:-8080}"
ENV_FILE=".env"
RUN_FETCH=1
RUN_UP=1
ASSUME_YES=0
ADMIN_PASSWORD="${OPENMRS_ADMIN_PASSWORD:-Admin123}"
DB_PASSWORD="${OPENMRS_DB_PASSWORD:-Admin123}"
CONTAINER_NAME="${TENAOS_CONTAINER_NAME:-TenaOS_v1}"
IMAGE_NAME="${TENAOS_IMAGE_NAME:-tenaos}"

log() { printf '[setup-demo] %s\n' "$*"; }
die() { printf '[setup-demo] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Progress UX for the two long, otherwise-silent wait loops ────────────
# Interactive terminals get a single self-overwriting spinner line so it's
# visually obvious the script is still alive; non-interactive runs (log
# files, CI) keep the original periodic plain-text lines instead, since a
# carriage-return spinner would just corrupt a saved log.
is_tty() { [ -t 1 ]; }

SPIN_FRAMES='|/-\'

progress_tick() {
  # progress_tick <spin-index> <label> <elapsed-seconds>
  is_tty || return 0
  local idx="$1" label="$2" elapsed="$3"
  printf '\r[setup-demo] %s %s... (%ds elapsed)   ' \
    "${SPIN_FRAMES:$((idx % ${#SPIN_FRAMES})):1}" "$label" "$elapsed"
}

progress_done() {
  # progress_done <label> <elapsed-seconds>
  local label="$1" elapsed="$2"
  if is_tty; then
    printf '\r[setup-demo] [OK] %s (%ds)                                        \n' "$label" "$elapsed"
  else
    log "$label after ${elapsed}s."
  fi
}

progress_clear() {
  is_tty || return 0
  printf '\r%80s\r' ' '
}

usage() {
  cat <<'EOF'
Usage: bash scripts/setup-demo.sh [options]

Options:
  --target-dir DIR          Artifact download directory (default: ./tenaos-bootstrap)
  --port PORT               Host port for the TenaOS web UI (default: 8080)
  --admin-password PASS     OpenMRS admin/service password (default: Admin123)
  --db-password PASS        MariaDB openmrs user password (default: Admin123)
  --env-file FILE           Env file to write (default: .env)
  --skip-fetch              Do not run scripts/fetch-models.sh; only validate existing artifacts
  --skip-up                 Do not launch Docker Compose
  -y, --yes                 Update an existing env file without prompting
  -h, --help                Show this help

Environment overrides are also honored: TENAOS_BOOTSTRAP_DIR,
TENAOS_HOST_PORT, TENAOS_CONTAINER_NAME, TENAOS_IMAGE_NAME,
OPENMRS_ADMIN_PASSWORD, OPENMRS_DB_PASSWORD.

Running a second instance alongside an existing one on the same host?
Set TENAOS_CONTAINER_NAME, TENAOS_IMAGE_NAME, and COMPOSE_PROJECT_NAME to
distinct values and --port to a free port. See "Running Multiple
Instances" in README.md.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target-dir) TARGET_DIR="$2"; shift 2 ;;
    --port) HOST_PORT="$2"; shift 2 ;;
    --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
    --db-password) DB_PASSWORD="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --skip-fetch) RUN_FETCH=0; shift ;;
    --skip-up) RUN_UP=0; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

compose_cmd=()
detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    compose_cmd=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    compose_cmd=(docker-compose)
  else
    die "Docker Compose is required. Install the Docker Compose plugin or docker-compose."
  fi
}

generate_password() {
  if command -v openssl >/dev/null 2>&1; then
    printf 'TenaOS-%s-aA1' "$(openssl rand -hex 9)"
  else
    printf 'TenaOS-%s-aA1' "$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 18)"
  fi
}

validate_password() {
  local name="$1" password="$2"
  if [ "${#password}" -lt 8 ] ||
     [[ ! "$password" =~ [[:lower:]] ]] ||
     [[ ! "$password" =~ [[:upper:]] ]] ||
     [[ ! "$password" =~ [[:digit:]] ]]; then
    die "$name must be at least 8 characters and include uppercase, lowercase, and a digit."
  fi
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

check_port_available() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    if ss -ltn "( sport = :$port )" | awk 'NR > 1 { found = 1 } END { exit found ? 0 : 1 }'; then
      die "port $port is already in use. Re-run with --port <free-port>."
    fi
  elif command -v lsof >/dev/null 2>&1; then
    if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
      die "port $port is already in use. Re-run with --port <free-port>."
    fi
  else
    log "WARN: neither ss nor lsof is available; skipping port availability check."
  fi
}

check_gpu() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    die "nvidia-smi not found. Install NVIDIA drivers and nvidia-container-toolkit before running TenaOS."
  fi
  nvidia-smi >/dev/null || die "nvidia-smi failed. Check NVIDIA driver/GPU availability."
  if docker info 2>/dev/null | grep -qi nvidia; then
    log "Docker reports NVIDIA runtime support."
  else
    log "WARN: Docker info did not list NVIDIA runtime. Compose may still work with device reservations, but verify nvidia-container-toolkit."
  fi
}

write_env_value() {
  local file="$1" key="$2" value="$3"
  if grep -q "^${key}=" "$file"; then
    python3 - "$file" "$key" "$value" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = path.read_text().splitlines()
out = [f"{key}={value}" if line.startswith(key + "=") else line for line in lines]
path.write_text("\n".join(out) + "\n")
PY
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

validate_artifacts() {
  local models_dir="$1" embed_dir="$2" ciel_file="$3" snapshots_dir="$4" sapbert_dir="$5"
  [ -f "$models_dir/gemma-4-E4B-it-BF16.gguf" ] || die "missing Gemma GGUF at $models_dir/gemma-4-E4B-it-BF16.gguf"
  [ -f "$models_dir/mmproj-gemma-4-E4B-it-bf16.gguf" ] || die "missing mmproj GGUF at $models_dir/mmproj-gemma-4-E4B-it-bf16.gguf"
  [ -f "$embed_dir/config.json" ] || die "missing EmbedGemma config at $embed_dir/config.json"
  [ -f "$ciel_file" ] || die "missing CIEL SQLite at $ciel_file"
  [ -f "$snapshots_dir/who_msf_guidelines.snapshot" ] || die "missing WHO/MSF Qdrant snapshot"
  [ -f "$snapshots_dir/ciel_concepts.snapshot" ] || die "missing CIEL Qdrant snapshot"
  [ -f "$sapbert_dir/config.json" ] || die "missing SapBERT config at $sapbert_dir/config.json"
}

wait_for_container_healthy() {
  local timeout_seconds="${TENAOS_SETUP_HEALTH_TIMEOUT_SECONDS:-1200}"
  local interval_seconds=10
  local elapsed=0
  local spin_i=0
  log "[4/5] Waiting for Docker healthcheck on $CONTAINER_NAME (OpenMRS first boot can take several minutes) ..."
  while [ "$elapsed" -le "$timeout_seconds" ]; do
    local status
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_NAME" 2>/dev/null || true)"
    case "$status" in
      healthy)
        progress_done "Container is healthy" "$elapsed"
        return 0
        ;;
      unhealthy)
        progress_clear
        docker logs --tail 80 "$CONTAINER_NAME" >&2 || true
        die "$CONTAINER_NAME became unhealthy. Review the logs above."
        ;;
      "")
        progress_tick "$spin_i" "Waiting for $CONTAINER_NAME to be created" "$elapsed"
        ;;
      *)
        progress_tick "$spin_i" "Starting ($status)" "$elapsed"
        if ! is_tty && [ $((elapsed % 30)) -eq 0 ]; then
          log "Still starting ($status, elapsed ${elapsed}s)."
        fi
        ;;
    esac
    spin_i=$((spin_i + 1))
    sleep "$interval_seconds"
    elapsed=$((elapsed + interval_seconds))
  done
  progress_clear
  docker logs --tail 120 "$CONTAINER_NAME" >&2 || true
  die "$CONTAINER_NAME did not become healthy within ${timeout_seconds}s."
}

wait_for_openmrs_auth_ready() {
  local timeout_seconds="${TENAOS_SETUP_OPENMRS_TIMEOUT_SECONDS:-300}"
  local interval_seconds=5
  local elapsed=0
  local spin_i=0
  local url="http://127.0.0.1:${HOST_PORT}/openmrs/ws/rest/v1/session"
  log "[5/5] Verifying OpenMRS login readiness through http://localhost:${HOST_PORT} ..."
  while [ "$elapsed" -le "$timeout_seconds" ]; do
    # curl's own -S error text (e.g. "Operation timed out") is expected and
    # noisy while OpenMRS is still coming up; only the parsed JSON result
    # matters here, so curl's stderr is discarded rather than left to leak
    # a "successful failure" onto the user's terminal on every poll.
    if curl -fsS -u "admin:${ADMIN_PASSWORD}" --connect-timeout 3 --max-time 10 "$url" 2>/dev/null \
      | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("authenticated") is True else 1)' \
      >/dev/null 2>&1; then
      progress_done "OpenMRS REST authentication ready" "$elapsed"
      return 0
    fi
    progress_tick "$spin_i" "Waiting for OpenMRS to finish booting" "$elapsed"
    if ! is_tty && [ $((elapsed % 30)) -eq 0 ]; then
      log "OpenMRS REST is still warming up (elapsed ${elapsed}s)."
    fi
    spin_i=$((spin_i + 1))
    sleep "$interval_seconds"
    elapsed=$((elapsed + interval_seconds))
  done
  progress_clear
  docker logs --tail 120 "$CONTAINER_NAME" >&2 || true
  die "OpenMRS REST authentication did not become ready within ${timeout_seconds}s."
}

log "[1/5] Checking Docker, GPU, and port availability ..."
require_command docker
require_command python3
require_command curl
detect_compose
check_gpu
check_port_available "$HOST_PORT"

validate_password OPENMRS_ADMIN_PASSWORD "$ADMIN_PASSWORD"
validate_password OPENMRS_DB_PASSWORD "$DB_PASSWORD"
log "[1/5] Prerequisites OK."

if [ "$RUN_FETCH" -eq 1 ]; then
  log "[2/5] Fetching model and knowledge-base artifacts into $TARGET_DIR ..."
  bash scripts/fetch-models.sh "$TARGET_DIR"
  log "[2/5] Artifacts ready."
else
  log "[2/5] Skipping artifact fetch; validating existing artifacts in $TARGET_DIR ..."
fi

MODELS_DIR="$TARGET_DIR/models"
EMBED_DIR="$TARGET_DIR/embedgemma-300m"
CIEL_FILE="$TARGET_DIR/ciel/ciel_search.sqlite3"
SNAPSHOTS_DIR="$TARGET_DIR/qdrant-snapshots"
SAPBERT_DIR="$TARGET_DIR/sapbert"
validate_artifacts "$MODELS_DIR" "$EMBED_DIR" "$CIEL_FILE" "$SNAPSHOTS_DIR" "$SAPBERT_DIR"

if [ -f "$ENV_FILE" ] && [ "$ASSUME_YES" -ne 1 ]; then
  die "$ENV_FILE already exists. Re-run with --yes to update it, or choose --env-file."
fi
if [ ! -f "$ENV_FILE" ]; then
  cp demo.env.example "$ENV_FILE"
fi

write_env_value "$ENV_FILE" TENAOS_PUBLIC_HOST "localhost"
write_env_value "$ENV_FILE" TENAOS_HOST_PORT "$HOST_PORT"
write_env_value "$ENV_FILE" TENAOS_CONTAINER_NAME "$CONTAINER_NAME"
write_env_value "$ENV_FILE" TENAOS_IMAGE_NAME "$IMAGE_NAME"
write_env_value "$ENV_FILE" TENAOS_PROFILE "demo"
write_env_value "$ENV_FILE" OPENMRS_DB_PASSWORD "$DB_PASSWORD"
write_env_value "$ENV_FILE" OPENMRS_ADMIN_PASSWORD "$ADMIN_PASSWORD"
write_env_value "$ENV_FILE" OPENMRS_HEALTHCHECK_USERNAME "admin"
write_env_value "$ENV_FILE" OPENMRS_HEALTHCHECK_PASSWORD "$ADMIN_PASSWORD"
write_env_value "$ENV_FILE" OPENMRS_SERVICE_USER "admin"
write_env_value "$ENV_FILE" OPENMRS_SERVICE_PASSWORD "$ADMIN_PASSWORD"
write_env_value "$ENV_FILE" OPENMRS_JAVA_MEMORY_OPTS "-Xmx4g"
write_env_value "$ENV_FILE" TENAOS_SEED_DEMO_PATIENTS "true"
write_env_value "$ENV_FILE" TENAOS_DEMO_PATIENT_COUNT "50"
write_env_value "$ENV_FILE" TENAOS_MODELS_PATH "$MODELS_DIR"
write_env_value "$ENV_FILE" TENAOS_EMBED_MODEL_PATH "$EMBED_DIR"
write_env_value "$ENV_FILE" TENAOS_CIEL_SQLITE_PATH "$CIEL_FILE"
write_env_value "$ENV_FILE" TENAOS_QDRANT_SNAPSHOTS_PATH "$SNAPSHOTS_DIR"
write_env_value "$ENV_FILE" TENAOS_SAPBERT_PATH "$SAPBERT_DIR"

log "Wrote $ENV_FILE."
log "OpenMRS username: admin"
log "OpenMRS password: $ADMIN_PASSWORD"

if [ "$RUN_UP" -eq 1 ]; then
  log "[3/5] Building the TenaOS image and starting containers with ${compose_cmd[*]} up -d"
  log "[3/5] (first build compiles llama.cpp from source and can take a while; later runs reuse Docker's layer cache) ..."
  "${compose_cmd[@]}" --env-file "$ENV_FILE" up -d
  log "[3/5] Containers started."
  wait_for_container_healthy
  wait_for_openmrs_auth_ready
  log "Setup complete."
  log "Open http://localhost:$HOST_PORT"
  log "Sign in with admin / $ADMIN_PASSWORD"
else
  log "Skipping compose launch because --skip-up was provided."
fi
