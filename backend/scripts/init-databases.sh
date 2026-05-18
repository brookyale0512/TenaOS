#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/tenaos-common.sh"

log() { echo "[init-databases] $(date '+%H:%M:%S') $*"; }

tenaos_apply_defaults
tenaos_compute_urls
tenaos_require_runtime_secrets

MARIADB_WAIT_MAX="${MARIADB_WAIT_MAX:-60}"

log "Waiting for MariaDB..."
for _ in $(seq 1 "$MARIADB_WAIT_MAX"); do
  if mysqladmin ping --silent 2>/dev/null; then
    break
  fi
  sleep 2
done
mysqladmin ping --silent 2>/dev/null || { log "ERROR: MariaDB never became ready"; exit 1; }
log "MariaDB is ready."

log "Creating MariaDB OpenMRS database and user..."
mysql -u root <<MYSQL
CREATE DATABASE IF NOT EXISTS \`${OPENMRS_DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${OPENMRS_DB_USER}'@'localhost' IDENTIFIED BY '${OPENMRS_DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${OPENMRS_DB_USER}'@'127.0.0.1' IDENTIFIED BY '${OPENMRS_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${OPENMRS_DB_NAME}\`.* TO '${OPENMRS_DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${OPENMRS_DB_NAME}\`.* TO '${OPENMRS_DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
MYSQL

log "Database initialization complete."
