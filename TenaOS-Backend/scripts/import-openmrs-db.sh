#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/tenaos-common.sh"

usage() {
  cat >&2 <<'USAGE'
Usage: ./scripts/import-openmrs-db.sh /path/to/openmrs.sql

Replaces the OpenMRS database inside the TenaOS container with the
provided MariaDB dump. The script stops OpenMRS, backs up the current database,
recreates the target database, imports the dump, restores the image-bundled
module set into the data volume, and restarts OpenMRS.
USAGE
}

[ "${1:-}" ] || { usage; exit 2; }

SQL_DUMP="$1"
[ -f "$SQL_DUMP" ] || tenaos_fail "SQL dump not found: $SQL_DUMP"

tenaos_load_env
tenaos_require_runtime_secrets

BACKUP_DIR="${TENAOS_OPENMRS_BACKUP_DIR:-$TENAOS_ROOT_DIR/runtime-artifacts/openmrs/backups}"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="$BACKUP_DIR/openmrs_pre_import_$(date -u +%Y%m%dT%H%M%SZ).sql"

tenaos_log "Stopping OpenMRS web process..."
docker exec "$TENAOS_CONTAINER_NAME" supervisorctl stop openmrs >/dev/null || true

tenaos_log "Backing up current database to $BACKUP_FILE..."
docker exec "$TENAOS_CONTAINER_NAME" mysqldump -u root --single-transaction --routines --triggers "$OPENMRS_DB_NAME" > "$BACKUP_FILE"

tenaos_log "Recreating $OPENMRS_DB_NAME database..."
docker exec "$TENAOS_CONTAINER_NAME" mysql -u root <<SQL
DROP DATABASE IF EXISTS \`${OPENMRS_DB_NAME}\`;
CREATE DATABASE \`${OPENMRS_DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${OPENMRS_DB_USER}'@'localhost' IDENTIFIED BY '${OPENMRS_DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${OPENMRS_DB_USER}'@'127.0.0.1' IDENTIFIED BY '${OPENMRS_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`${OPENMRS_DB_NAME}\`.* TO '${OPENMRS_DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${OPENMRS_DB_NAME}\`.* TO '${OPENMRS_DB_USER}'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

tenaos_log "Importing $SQL_DUMP..."
docker exec -i "$TENAOS_CONTAINER_NAME" mysql -u root "$OPENMRS_DB_NAME" < "$SQL_DUMP"

tenaos_log "Restoring image-bundled OpenMRS module set into the data volume..."
docker exec "$TENAOS_CONTAINER_NAME" sh -c '
  set -e
  rm -rf /opt/openmrs/data/modules/* /opt/openmrs/data/modules.disabled/* 2>/dev/null || true
  mkdir -p /opt/openmrs/data/modules
  cp -R /opt/openmrs/distribution/openmrs_modules/. /opt/openmrs/data/modules/
  chown -R tenaos:tenaos /opt/openmrs/data/modules
  rm -rf /usr/local/tomcat/work/* /usr/local/tomcat/temp/*
'

tenaos_log "Starting OpenMRS web process..."
docker exec "$TENAOS_CONTAINER_NAME" supervisorctl start openmrs >/dev/null

tenaos_log "Import complete. Backup written to $BACKUP_FILE"
