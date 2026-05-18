#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/tenaos-common.sh"

MODE="host"
QUIET=0
WRITE_SMOKE=0
for arg in "$@"; do
  case "$arg" in
    --internal) MODE="internal" ;;
    --healthcheck) QUIET=1 ;;
    --write) WRITE_SMOKE=1 ;;
    *) tenaos_fail "Unknown argument: $arg" ;;
  esac
done

if [ "$MODE" = "host" ]; then
  tenaos_load_env
  OPENMRS_BASE="http://localhost:${OPENMRS_PORT:-18080}"
else
  tenaos_apply_defaults
  OPENMRS_BASE="http://localhost:8080"
fi

HTTP_CONNECT_TIMEOUT_SECONDS="${TENAOS_VERIFY_HTTP_CONNECT_TIMEOUT_SECONDS:-5}"
HTTP_MAX_TIME_SECONDS="${TENAOS_VERIFY_HTTP_MAX_TIME_SECONDS:-15}"

# Healthchecks default to a least-privilege user (configurable). Only the
# write-smoke (--write) and host-mode (admin diagnostic) flows need higher
# privileges.
if [ "$WRITE_SMOKE" -eq 1 ] || [ "$MODE" = "host" ]; then
  : "${OPENMRS_VERIFY_USERNAME:=admin}"
  : "${OPENMRS_VERIFY_PASSWORD:=${OPENMRS_ADMIN_PASSWORD:-}}"
else
  : "${OPENMRS_VERIFY_USERNAME:=tenaos-healthcheck}"
  : "${OPENMRS_VERIFY_PASSWORD:=}"
fi

if [ -z "${OPENMRS_VERIFY_PASSWORD}" ]; then
  echo "OPENMRS_VERIFY_PASSWORD must be provided (set in environment or backend/.env)." >&2
  exit 2
fi

CURL_AUTH=(-u "${OPENMRS_VERIFY_USERNAME}:${OPENMRS_VERIFY_PASSWORD}")

report() { [ "$QUIET" -eq 1 ] || echo "$1"; }
fail() { echo "$1" >&2; exit 1; }

check_status() {
  local name="$1" url="$2" expected="$3" actual
  actual=$(curl -sS "${CURL_AUTH[@]}" --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" -o /dev/null -w '%{http_code}' "$url") || fail "$name failed to connect"
  [ "$actual" = "$expected" ] || fail "$name returned $actual, expected $expected"
  report "$name ok ($actual)"
}

check_json_count() {
  local name="$1" url="$2" minimum="$3"
  local count
  count=$(curl -sS "${CURL_AUTH[@]}" --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" "$url" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(len(data.get("results", [])))') || fail "$name failed"
  [ "$count" -ge "$minimum" ] || fail "$name returned $count result(s), expected at least $minimum"
  report "$name ok ($count result(s))"
}

# Creates a synthetic patient via /idgen + /patient, opens it, then voids
# (purges) it to leave the DB clean. Run sparingly.
write_smoke() {
  local id_source_uuid identifier first_name="TenaOS" last_name="Smoke-${RANDOM}"
  id_source_uuid=$(curl -sS "${CURL_AUTH[@]}" --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" \
    "$OPENMRS_BASE/openmrs/ws/rest/v1/idgen/identifiersource?v=full" \
    | python3 -c 'import json,sys; data=json.load(sys.stdin); print(next((s["uuid"] for s in data.get("results", []) if s.get("autoGenerationOption", {}).get("automaticGenerationEnabled")), ""))')

  if [ -z "$id_source_uuid" ]; then
    fail "Write smoke: no auto-generating identifier source available"
  fi

  identifier=$(curl -sS "${CURL_AUTH[@]}" -H 'Content-Type: application/json' \
    --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" \
    -X POST -d '{}' \
    "$OPENMRS_BASE/openmrs/ws/rest/v1/idgen/identifiersource/${id_source_uuid}/identifier" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("identifier",""))')

  [ -n "$identifier" ] || fail "Write smoke: idgen returned empty identifier"

  local id_type_uuid
  id_type_uuid=$(curl -sS "${CURL_AUTH[@]}" --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" \
    "$OPENMRS_BASE/openmrs/ws/rest/v1/idgen/identifiersource/${id_source_uuid}?v=full" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("identifierType", {}).get("uuid", ""))')

  local location_uuid
  location_uuid=$(curl -sS "${CURL_AUTH[@]}" --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" \
    "$OPENMRS_BASE/openmrs/ws/rest/v1/location?limit=1" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("results",[]); print(r[0]["uuid"] if r else "")')

  [ -n "$id_type_uuid" ] && [ -n "$location_uuid" ] || fail "Write smoke: missing identifier type or location"

  local payload
  payload=$(python3 -c "
import json
print(json.dumps({
  'identifiers': [{
    'identifierType': '${id_type_uuid}',
    'identifier': '${identifier}',
    'location': '${location_uuid}',
    'preferred': True,
  }],
  'person': {
    'names': [{'givenName': '${first_name}', 'familyName': '${last_name}', 'preferred': True}],
    'gender': 'F',
    'birthdate': '1990-01-01',
    'birthdateEstimated': False,
  },
}))
")

  local patient_uuid
  patient_uuid=$(curl -sS "${CURL_AUTH[@]}" -H 'Content-Type: application/json' \
    --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" \
    -X POST -d "$payload" \
    "$OPENMRS_BASE/openmrs/ws/rest/v1/patient" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("uuid",""))')

  [ -n "$patient_uuid" ] || fail "Write smoke: patient creation returned no uuid"
  report "Write smoke patient created ($patient_uuid)"

  # Clean up (purge) so the smoke patient does not pollute the DB.
  curl -sS "${CURL_AUTH[@]}" --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" --max-time "$HTTP_MAX_TIME_SECONDS" \
    -X DELETE "$OPENMRS_BASE/openmrs/ws/rest/v1/patient/${patient_uuid}?purge=true" >/dev/null \
    || report "Write smoke: purge failed (left voided patient ${patient_uuid})"
}

report "Verifying TenaOS OpenMRS in $MODE mode..."
check_status "OpenMRS REST session" "$OPENMRS_BASE/openmrs/ws/rest/v1/session" "200"
check_status "OpenMRS FHIR metadata" "$OPENMRS_BASE/openmrs/ws/fhir2/R4/metadata" "200"

# Metadata primitives required by the phase 1 frontend.
check_json_count "OpenMRS locations" "$OPENMRS_BASE/openmrs/ws/rest/v1/location?limit=1" 1
check_json_count "OpenMRS identifier types" "$OPENMRS_BASE/openmrs/ws/rest/v1/patientidentifiertype?limit=1" 1
check_json_count "OpenMRS visit types" "$OPENMRS_BASE/openmrs/ws/rest/v1/visittype?limit=1" 1
check_json_count "OpenMRS forms" "$OPENMRS_BASE/openmrs/ws/rest/v1/form?limit=1" 1

if [ "$WRITE_SMOKE" -eq 1 ]; then
  report "Running write-side smoke..."
  write_smoke
fi

report "TenaOS OpenMRS verification passed."
