#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/tenaos-common.sh"

tenaos_apply_defaults
tenaos_compute_urls
tenaos_require_runtime_secrets

export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
export OMRS_HOME=/opt/openmrs
export OMRS_DB=mysql
export OMRS_DB_HOSTNAME=localhost
export OMRS_DB_PORT=3306
export OMRS_DB_NAME="$OPENMRS_DB_NAME"
export OMRS_DB_USERNAME="$OPENMRS_DB_USER"
export OMRS_DB_PASSWORD="$OPENMRS_DB_PASSWORD"
export OMRS_ADMIN_USER_PASSWORD="$OPENMRS_ADMIN_PASSWORD"
export OMRS_ADMIN_PASSWORD_LOCKED=false
export OMRS_AUTO_UPDATE_DATABASE=true
export OMRS_CREATE_DATABASE_USER=false
export OMRS_CREATE_TABLES=true
export OMRS_HAS_CURRENT_OPENMRS_DATABASE=true
export OMRS_INSTALL_METHOD=auto
export OMRS_WEBAPP_NAME=openmrs
export OMRS_MODULE_WEB_ADMIN=true
export OMRS_SEARCH=lucene
export OMRS_SEARCH_CONFIG=luceneConfig
export OMRS_DB_EXTRA_ARGS='&useSSL=false&allowPublicKeyRetrieval=true'
export OMRS_JAVA_SERVER_OPTS='-Dfile.encoding=UTF-8 -server -Djava.security.egd=file:/dev/./urandom -Djava.awt.headless=true -Djava.awt.headlesslib=true'
export OMRS_JAVA_MEMORY_OPTS="$OPENMRS_JAVA_MEMORY_OPTS"
export OMRS_KEYCLOAK_ENABLED=false
export OMRS_EXTRA_HIBERNATE_SEARCH_INDEXING_PLAN_SYNCHRONIZATION_STRATEGY="${OMRS_EXTRA_HIBERNATE_SEARCH_INDEXING_PLAN_SYNCHRONIZATION_STRATEGY:-async}"

mkdir -p /opt/openmrs/data/modules /opt/openmrs/data/owa /opt/openmrs/data/configuration /opt/openmrs/data/frontend

OPENMRS_RUNTIME_PROPERTIES_FILE=/opt/openmrs/data/openmrs-runtime.properties
OPENMRS_MANAGED_RESTART_FLAG_FILE="${OPENMRS_MANAGED_RESTART_FLAG_FILE:-/opt/openmrs/data/.tenaos-managed-restart}"
OPENMRS_CONTROL_PLANE_CONFIG_DIR="${OPENMRS_CONTROL_PLANE_CONFIG_DIR:-/opt/tenaos/data/emr-os/openmrs-managed-config}"
OPENMRS_DISTRO_CORE=/opt/openmrs/distribution/openmrs_core
OPENMRS_SERVER_PROPERTIES_FILE=/opt/openmrs/openmrs-server.properties
OPENMRS_MANAGED_RESTART_PROPERTIES_FILE=/opt/openmrs/openmrs-managed-restart.properties
TOMCAT_DIR=/usr/local/tomcat
TOMCAT_WEBAPPS_DIR="$TOMCAT_DIR/webapps"
TOMCAT_WORK_DIR="$TOMCAT_DIR/work"
TOMCAT_TEMP_DIR="$TOMCAT_DIR/temp"
TOMCAT_SETENV_FILE="$TOMCAT_DIR/bin/setenv.sh"

# Remove modules that are incompatible with this OpenMRS runtime:
#
# 1. oauth2login / authentication — would override the native auth scheme and
#    require Keycloak; without it OpenMRS redirects to /initialsetup.
#
# 2. legacyui — references PortletController which was removed from
#    openmrs-web 2.8.4. When loaded, it causes the Spring WebApplicationContext
#    to fail refresh, blocking webservices.rest and all dependent modules.
#
# 3. reporting / reportingrest / patientdocuments / htmlwidgets — the reporting
#    module contains ManageReportQueuePortletController which also extends the
#    removed PortletController class, producing the same context refresh failure.
#
# 4. addresshierarchy / patientflags — same PortletController reference found
#    via binary scan of the omods; both block the Spring context refresh.
#
# The patch-openmrs-webservices.py build-time script already strips legacyui
# awareness edges from all other omods so they start cleanly without it.
for _omod in \
  oauth2login \
  authentication \
  legacyui \
  reporting \
  reportingrest \
  patientdocuments \
  htmlwidgets \
  addresshierarchy \
  patientflags; do
  rm -f "/opt/openmrs/distribution/openmrs_modules/${_omod}-"*.omod \
        "/opt/openmrs/distribution/openmrs_modules/${_omod}.omod" \
        "/opt/openmrs/data/modules/${_omod}-"*.omod \
        "/opt/openmrs/data/modules/${_omod}.omod"
done
unset _omod
rm -f /opt/openmrs/data/oauth2.properties

tenaos_overlay_managed_openmrs_config() {
  if [ ! -d "$OPENMRS_CONTROL_PLANE_CONFIG_DIR" ]; then
    return
  fi
  local domain
  for domain in "$OPENMRS_CONTROL_PLANE_CONFIG_DIR"/*; do
    [ -d "$domain" ] || continue
    mkdir -p "/opt/openmrs/data/configuration/$(basename "$domain")"
    rm -rf "/opt/openmrs/data/configuration/$(basename "$domain")/tenaos-managed"
    cp -R "$domain/." "/opt/openmrs/data/configuration/$(basename "$domain")/"
  done
}

tenaos_prepare_managed_restart_properties() {
  if [ -f "$OPENMRS_SERVER_PROPERTIES_FILE" ]; then
    awk '
      BEGIN { replaced = 0 }
      /^auto_update_database=/ { print "auto_update_database=false"; replaced = 1; next }
      { print }
      END { if (replaced == 0) print "auto_update_database=false" }
    ' "$OPENMRS_SERVER_PROPERTIES_FILE" > "$OPENMRS_MANAGED_RESTART_PROPERTIES_FILE"
  else
    printf 'auto_update_database=false\n' > "$OPENMRS_MANAGED_RESTART_PROPERTIES_FILE"
  fi
}

# Wait until the OpenMRS REST `session` resource returns 200 with admin
# Basic auth, indicating Tomcat has finished module startup and native REST
# authentication is usable. Unauthenticated requests can legitimately return
# 302 during/after first setup, so they are not a reliable readiness signal.
tenaos_wait_for_openmrs_rest_ready() {
  local timeout_seconds="${OPENMRS_REST_READY_TIMEOUT_SECONDS:-600}"
  local interval_seconds="${OPENMRS_REST_READY_INTERVAL_SECONDS:-5}"
  local elapsed=0
  while [ "$elapsed" -lt "$timeout_seconds" ]; do
    local code
    code=$(curl -sS -u "admin:${OMRS_ADMIN_USER_PASSWORD}" -o /dev/null -w '%{http_code}' \
      --connect-timeout 3 --max-time 8 \
      "http://localhost:8080/${OMRS_WEBAPP_NAME}/ws/rest/v1/session" || echo 000)
    if [ "$code" = "200" ]; then
      echo "OpenMRS REST ready after ${elapsed}s"
      return 0
    fi
    sleep "$interval_seconds"
    elapsed=$((elapsed + interval_seconds))
    if [ $((elapsed % 30)) -eq 0 ]; then
      echo "Still waiting for OpenMRS REST (last status: ${code}, elapsed: ${elapsed}s)"
    fi
  done
  echo "OpenMRS REST did not become ready within ${timeout_seconds}s" >&2
  return 1
}

tenaos_wait_for_first_boot_install() {
  local timeout_seconds="${OPENMRS_FIRST_BOOT_INSTALL_TIMEOUT_SECONDS:-600}"
  local interval_seconds="${OPENMRS_FIRST_BOOT_INSTALL_INTERVAL_SECONDS:-5}"
  local elapsed=0
  while [ "$elapsed" -lt "$timeout_seconds" ]; do
    if [ -f "$OPENMRS_RUNTIME_PROPERTIES_FILE" ] &&
       grep -q "Done refreshing Context" /opt/openmrs/data/openmrs.log 2>/dev/null; then
      echo "OpenMRS first-boot install completed after ${elapsed}s"
      return 0
    fi
    sleep "$interval_seconds"
    elapsed=$((elapsed + interval_seconds))
    if [ $((elapsed % 30)) -eq 0 ]; then
      echo "Still waiting for OpenMRS first-boot install (elapsed: ${elapsed}s)"
    fi
  done
  echo "OpenMRS first-boot install did not complete within ${timeout_seconds}s" >&2
  return 1
}

tenaos_stop_tomcat_processes() {
  local wrapper_pid="${1:-}"
  if [ -n "$wrapper_pid" ]; then
    kill -TERM "$wrapper_pid" 2>/dev/null || true
    wait "$wrapper_pid" 2>/dev/null || true
  fi
  local pids
  pids="$(pgrep -f 'org.apache.catalina.startup.Bootstrap' || true)"
  if [ -n "$pids" ]; then
    echo "Stopping existing Tomcat JVM(s): $pids"
    kill -TERM $pids 2>/dev/null || true
    local elapsed=0
    while [ "$elapsed" -lt 30 ] && pgrep -f 'org.apache.catalina.startup.Bootstrap' >/dev/null 2>&1; do
      sleep 1
      elapsed=$((elapsed + 1))
    done
    pids="$(pgrep -f 'org.apache.catalina.startup.Bootstrap' || true)"
    if [ -n "$pids" ]; then
      echo "Force-stopping Tomcat JVM(s): $pids"
      kill -KILL $pids 2>/dev/null || true
    fi
  fi
}

tenaos_run_existing_openmrs() {
  echo "Running lightweight OpenMRS restart using existing data directory"
  /openmrs/wait-for-it.sh -t 3600 -h "$OMRS_DB_HOSTNAME" -p "$OMRS_DB_PORT"
  rm -fR "${TOMCAT_WEBAPPS_DIR:?}"/* "${TOMCAT_WORK_DIR:?}"/* "${TOMCAT_TEMP_DIR:?}"/*
  cp -r "${OPENMRS_DISTRO_CORE}/." "${TOMCAT_WEBAPPS_DIR}"
  tenaos_overlay_managed_openmrs_config
  tenaos_prepare_managed_restart_properties
  JAVA_OPTS="$OMRS_JAVA_SERVER_OPTS -Dinitializer.domains=!ocl"
  CATALINA_OPTS="${OMRS_JAVA_MEMORY_OPTS} -DOPENMRS_INSTALLATION_SCRIPT=${OPENMRS_MANAGED_RESTART_PROPERTIES_FILE} -DOPENMRS_APPLICATION_DATA_DIRECTORY=/opt/openmrs/data/"
  cat > "$TOMCAT_SETENV_FILE" <<EOF
export JAVA_OPTS="$JAVA_OPTS"
export CATALINA_OPTS="$CATALINA_OPTS"
EOF
  /usr/local/tomcat/bin/catalina.sh run &
  local tomcat_pid=$!
  if ! tenaos_wait_for_openmrs_rest_ready; then
    echo "OpenMRS startup failed REST readiness check; forwarding container exit." >&2
    kill -TERM "$tomcat_pid" 2>/dev/null || true
    wait "$tomcat_pid" || true
    exit 1
  fi
  # Seed runs on every boot path; gated by the marker file so it's a no-op
  # after the first successful run.
  tenaos_seed_locations_once || true
  wait "$tomcat_pid"
}

TENAOS_LOCATION_SEED_MARKER_FILE="${TENAOS_LOCATION_SEED_MARKER_FILE:-/opt/openmrs/data/.tenaos-locations-seeded}"

# Idempotently seed real-name OpenMRS locations after Tomcat is REST-ready.
# Marker file gates this so we run on first boot only; the script itself is
# also idempotent so re-running by hand is always safe.
tenaos_seed_locations_once() {
  if [ -f "$TENAOS_LOCATION_SEED_MARKER_FILE" ]; then
    return 0
  fi
  if ! OPENMRS_VERIFY_USERNAME=admin \
       OPENMRS_VERIFY_PASSWORD="$OMRS_ADMIN_USER_PASSWORD" \
       "$SCRIPT_DIR/seed-locations.sh" --internal; then
    echo "[tenaos] WARN: seed-locations.sh failed; will retry next boot." >&2
    return 0
  fi
  : > "$TENAOS_LOCATION_SEED_MARKER_FILE"
}

if [ -f "$OPENMRS_MANAGED_RESTART_FLAG_FILE" ] && [ -f "$OPENMRS_RUNTIME_PROPERTIES_FILE" ]; then
  tenaos_run_existing_openmrs
  exit 0
fi

# First boot: stage lite metadata, then delegate to the upstream OpenMRS
# startup script in the background so we can run our post-boot seeders against
# the live REST API once it's ready.
tenaos_overlay_managed_openmrs_config
cd /opt/openmrs/data

/openmrs/startup.sh &
STARTUP_PID=$!

if ! tenaos_wait_for_first_boot_install; then
  echo "OpenMRS first-boot setup failed; forwarding container exit." >&2
  tenaos_stop_tomcat_processes "$STARTUP_PID"
  exit 1
fi

# The upstream first-boot path writes runtime properties but the same Tomcat
# process can remain on /initialsetup. Restart once into the managed path so
# REST auth and health checks see the initialized runtime properties.
touch "$OPENMRS_MANAGED_RESTART_FLAG_FILE"
tenaos_stop_tomcat_processes "$STARTUP_PID"
tenaos_run_existing_openmrs
