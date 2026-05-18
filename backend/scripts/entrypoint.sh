#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/tenaos-common.sh"

log() { echo "[entrypoint] $(date '+%H:%M:%S') $*"; }

tenaos_apply_defaults
tenaos_compute_urls
tenaos_require_runtime_secrets

log "Preparing runtime directories..."
mkdir -p \
  /opt/openmrs/data/modules \
  /opt/openmrs/data/owa \
  /opt/openmrs/data/configuration \
  /opt/openmrs/data/frontend \
  /opt/tenaos/data/emr-os/openmrs-managed-config \
  /var/lib/lucene_index \
  /var/log/supervisor \
  /var/log/openmrs \
  /run/mysqld \
  /var/lib/mysql

log "Preparing ownership..."
chown -R tenaos:tenaos \
  /opt/openmrs \
  /opt/tomcat-openmrs \
  /opt/tenaos/data \
  /var/lib/lucene_index \
  /var/log/openmrs
chown -R mysql:mysql /run/mysqld /var/lib/mysql

if [ ! -d /var/lib/mysql/mysql ]; then
  log "Initializing MariaDB data directory..."
  mariadb-install-db --user=mysql --datadir=/var/lib/mysql --auth-root-authentication-method=normal 2>/dev/null
fi
chown -R mysql:mysql /run/mysqld /var/lib/mysql

log "Starting supervisord..."
exec /usr/bin/supervisord -c /opt/tenaos/configs/supervisor/supervisord.conf
