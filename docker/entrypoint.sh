#!/usr/bin/env bash
# TenaOS — single-container entrypoint.
# Validates required env, prepares runtime dirs, then hands off to supervisord.
set -euo pipefail

log() { printf '[tenaos] %s\n' "$*"; }

: "${OPENMRS_DB_PASSWORD:?OPENMRS_DB_PASSWORD must be set (use --env or docker compose)}"
: "${OPENMRS_ADMIN_PASSWORD:?OPENMRS_ADMIN_PASSWORD must be set (use --env or docker compose)}"
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
