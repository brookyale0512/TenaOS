#!/usr/bin/env bash
# TenaOS — single-container entrypoint.
# Validates required env, prepares runtime dirs, then hands off to supervisord.
set -euo pipefail

log() { printf '[tenaos] %s\n' "$*"; }

: "${OPENMRS_DB_PASSWORD:?OPENMRS_DB_PASSWORD must be set (use --env or docker compose)}"
: "${OPENMRS_ADMIN_PASSWORD:?OPENMRS_ADMIN_PASSWORD must be set (use --env or docker compose)}"

validate_openmrs_admin_password() {
  local password="$OPENMRS_ADMIN_PASSWORD"
  if [ "${#password}" -lt 8 ] ||
     [[ ! "$password" =~ [[:lower:]] ]] ||
     [[ ! "$password" =~ [[:upper:]] ]] ||
     [[ ! "$password" =~ [[:digit:]] ]]; then
    log "ERROR: OPENMRS_ADMIN_PASSWORD must be at least 8 characters and include uppercase, lowercase, and a digit."
    log "Example shape: Admin123. Rotate before any public deployment."
    exit 1
  fi
}

validate_openmrs_admin_password
export OPENMRS_VERIFY_USERNAME="${OPENMRS_VERIFY_USERNAME:-${OPENMRS_HEALTHCHECK_USERNAME:-admin}}"
export OPENMRS_VERIFY_PASSWORD="${OPENMRS_VERIFY_PASSWORD:-${OPENMRS_HEALTHCHECK_PASSWORD:-$OPENMRS_ADMIN_PASSWORD}}"
export OPENMRS_SERVICE_USER="${OPENMRS_SERVICE_USER:-admin}"
export OPENMRS_SERVICE_PASSWORD="${OPENMRS_SERVICE_PASSWORD:-$OPENMRS_ADMIN_PASSWORD}"

# ── Verify GGUF weights are mounted ──────────────────────────────────────
if [ ! -f /models/gemma-4-E4B-it-BF16.gguf ]; then
  log "ERROR: /models/gemma-4-E4B-it-BF16.gguf not found."
  log "Bind-mount your host models directory at /models, e.g.:"
  log "  -v \$(pwd)/models:/models:ro"
  exit 1
fi
log "Model weights present: $(ls -1 /models/*.gguf | wc -l) GGUF file(s)"

# ── Verify EmbedGemma is mounted ─────────────────────────────────────────
if [ ! -f /opt/tenaos/embedgemma-300m/config.json ]; then
  log "WARNING: EmbedGemma weights missing at /opt/tenaos/embedgemma-300m/."
  log "KB services will fail until you mount the model."
fi

# ── Verify CIEL SQLite is mounted ────────────────────────────────────────
if [ ! -f /opt/tenaos/ciel/ciel_search.sqlite3 ]; then
  log "WARNING: CIEL SQLite missing at /opt/tenaos/ciel/ciel_search.sqlite3."
  log "Concept lookup will fall back to limited mode."
fi

# ── Note about Qdrant snapshots (restored by the qdrant-restore program) ─
if [ -d /qdrant/snapshots ] && compgen -G '/qdrant/snapshots/*.snapshot' >/dev/null; then
  log "Qdrant snapshots present at /qdrant/snapshots; restore will run after Qdrant starts."
else
  log "NOTE: no Qdrant snapshots mounted at /qdrant/snapshots — KB collections will start empty."
  log "Fetch with scripts/fetch-models.sh and set TENAOS_QDRANT_SNAPSHOTS_PATH."
fi

# ── MariaDB first-run bootstrap ──────────────────────────────────────────
if [ ! -d /var/lib/mysql/mysql ]; then
  log "Initializing MariaDB data directory ..."
  mariadb-install-db --user=mysql --datadir=/var/lib/mysql >/dev/null
fi

# ── Runtime dirs ─────────────────────────────────────────────────────────
mkdir -p /opt/tenaos/runtime /var/log/tenaos /var/log/supervisor /run/mysqld
chown -R mysql:mysql /run/mysqld /var/lib/mysql
chown -R tenaos:tenaos /opt/tenaos/runtime /var/log/tenaos /opt/openmrs/data \
                      /opt/tomcat-openmrs 2>/dev/null || true

log "Handing off to supervisord (8 services)."
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
