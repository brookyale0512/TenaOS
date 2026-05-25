#!/bin/bash
# seed-locations.sh — idempotently seed real-name OpenMRS Locations into the
# running OpenMRS instance and tag each one with the canonical "Login Location"
# tag. Runs once on first OpenMRS boot via run-openmrs.sh; also safely
# re-runnable by hand against existing deployments (skips already-present
# locations and tags).
#
# Override the seed set with TENAOS_SEED_LOCATIONS="Outpatient,Inpatient,...".
# Override the tag with TENAOS_LOGIN_LOCATION_TAG (defaults to "Login Location").
#
# Auth follows the same pattern as verify-lite.sh:
#   OPENMRS_VERIFY_USERNAME / OPENMRS_VERIFY_PASSWORD (admin needed for writes).

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/tenaos-common.sh"

MODE="host"
QUIET=0
for arg in "$@"; do
  case "$arg" in
    --internal) MODE="internal" ;;
    --quiet) QUIET=1 ;;
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

: "${OPENMRS_VERIFY_USERNAME:=admin}"
: "${OPENMRS_VERIFY_PASSWORD:=${OPENMRS_ADMIN_PASSWORD:-}}"

if [ -z "${OPENMRS_VERIFY_PASSWORD}" ]; then
  echo "OPENMRS_VERIFY_PASSWORD must be provided (set in environment or backend/.env)." >&2
  exit 2
fi

CURL_AUTH=(-u "${OPENMRS_VERIFY_USERNAME}:${OPENMRS_VERIFY_PASSWORD}")
REST="$OPENMRS_BASE/openmrs/ws/rest/v1"

LOGIN_LOCATION_TAG_NAME="${TENAOS_LOGIN_LOCATION_TAG:-Login Location}"
SEED_LOCATIONS_RAW="${TENAOS_SEED_LOCATIONS:-Outpatient,Inpatient,Mobile Clinic,Community Outreach}"

# report() writes to stderr so functions can return UUIDs via stdout-capture
# (e.g. `TAG_UUID=$(resolve_login_location_tag_uuid)`) without log lines
# polluting the captured value.
report() { [ "$QUIET" -eq 1 ] || echo "[seed-locations] $1" >&2; }
fail() { echo "[seed-locations] ERROR: $1" >&2; exit 1; }

curl_get() {
  curl -sS "${CURL_AUTH[@]}" \
    --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" \
    --max-time "$HTTP_MAX_TIME_SECONDS" \
    "$@"
}

curl_post() {
  curl -sS "${CURL_AUTH[@]}" -H 'Content-Type: application/json' \
    --connect-timeout "$HTTP_CONNECT_TIMEOUT_SECONDS" \
    --max-time "$HTTP_MAX_TIME_SECONDS" \
    "$@"
}

json_quote() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

# --- 1. Resolve (or create) the Login Location tag ---------------------------

resolve_login_location_tag_uuid() {
  local tag_name_quoted; tag_name_quoted=$(json_quote "$LOGIN_LOCATION_TAG_NAME")
  local existing_uuid
  # /locationtag?q=... performs a partial match; we filter for exact `name`.
  existing_uuid=$(curl_get "$REST/locationtag?q=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$LOGIN_LOCATION_TAG_NAME")&v=default&limit=50" \
    | python3 -c "
import json, sys
target = $tag_name_quoted
data = json.load(sys.stdin)
for entry in data.get('results', []):
    if entry.get('name') == target and not entry.get('retired'):
        print(entry['uuid'])
        break
") || true

  if [ -n "$existing_uuid" ]; then
    report "Login Location tag already exists (${existing_uuid})"
    echo "$existing_uuid"
    return 0
  fi

  local payload
  payload=$(python3 -c "
import json
print(json.dumps({
  'name': $tag_name_quoted,
  'description': 'Locations that users may select as their session login location.',
}))
")
  local created_uuid
  created_uuid=$(curl_post -X POST -d "$payload" "$REST/locationtag" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("uuid",""))') || fail "Failed to create Login Location tag"
  [ -n "$created_uuid" ] || fail "Login Location tag creation returned empty uuid"
  report "Created Login Location tag (${created_uuid})"
  echo "$created_uuid"
}

# --- 2. Resolve an existing location by exact name ---------------------------

resolve_location_uuid_by_name() {
  local name="$1" name_quoted
  name_quoted=$(json_quote "$name")
  curl_get "$REST/location?q=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$name")&v=default&limit=50" \
    | python3 -c "
import json, sys
target = $name_quoted
data = json.load(sys.stdin)
for entry in data.get('results', []):
    if entry.get('name') == target and not entry.get('retired'):
        print(entry['uuid'])
        break
"
}

location_already_has_tag() {
  local location_uuid="$1" tag_uuid="$2"
  curl_get "$REST/location/${location_uuid}?v=full" \
    | python3 -c "
import json, sys
target = sys.argv[1]
data = json.load(sys.stdin)
for tag in data.get('tags', []) or []:
    if tag.get('uuid') == target:
        print('yes')
        break
" "$tag_uuid"
}

# --- 3. Create or update each seeded location --------------------------------

seed_one_location() {
  local name="$1" tag_uuid="$2"
  local existing_uuid
  existing_uuid=$(resolve_location_uuid_by_name "$name" || true)

  local location_uuid
  if [ -n "$existing_uuid" ]; then
    report "Location '$name' already exists (${existing_uuid})"
    location_uuid="$existing_uuid"
  else
    # OpenMRS REST `LocationResource1_8` has a documented quirk on CREATE: any
    # tags[] in the body are deserialized into transient LocationTag entities
    # (without their `name`) and then cascaded to saveLocation, which throws
    # "A tag name is required". On UPDATE the same payload resolves the tag
    # correctly. Workaround: create the bare location, then attach the tag in
    # a follow-up update — same path as the existing-location branch below.
    local payload
    payload=$(python3 -c "
import json, sys
print(json.dumps({
  'name': sys.argv[1],
  'description': sys.argv[1] + ' department/area',
}))
" "$name")
    # Up to 6 attempts with a 5s back-off to absorb post-boot races where
    # /session is 200 but writable resources haven't finished bootstrapping.
    local attempt=0 max_attempts=6 response
    while [ "$attempt" -lt "$max_attempts" ]; do
      response=$(curl_post -X POST -d "$payload" "$REST/location") || response=""
      location_uuid=$(printf '%s' "$response" | python3 -c 'import json,sys
try:
  print(json.load(sys.stdin).get("uuid",""))
except Exception:
  print("")' || true)
      if [ -n "$location_uuid" ]; then
        report "Created location '$name' (${location_uuid})"
        break
      fi
      attempt=$((attempt + 1))
      if [ "$attempt" -lt "$max_attempts" ]; then
        report "Create '$name' attempt $attempt returned no uuid; retrying in 5s"
        sleep 5
      fi
    done
    if [ -z "$location_uuid" ]; then
      echo "[seed-locations] Last response body for '$name': ${response}" >&2
      fail "Location creation for '$name' returned empty uuid after ${max_attempts} attempts"
    fi
  fi

  # Existing location: ensure it carries the Login Location tag.
  local has_tag
  has_tag=$(location_already_has_tag "$location_uuid" "$tag_uuid" || true)
  if [ "$has_tag" = "yes" ]; then
    return 0
  fi
  # Append the tag while preserving any existing tags. Use the nested
  # {uuid: "..."} reference form (see seed_one_location for context).
  # Up to 3 attempts because a freshly-created location occasionally lands in
  # a transient state where the first tag-update POST returns 200 but the tag
  # doesn't actually persist (we've reproduced this once on demo boot).
  local merge_attempt=0 merge_max=3
  while [ "$merge_attempt" -lt "$merge_max" ]; do
    local merged_tags
    merged_tags=$(curl_get "$REST/location/${location_uuid}?v=full" \
      | python3 -c "
import json, sys
new_tag = sys.argv[1]
data = json.load(sys.stdin)
existing = [t['uuid'] for t in (data.get('tags') or []) if t.get('uuid')]
if new_tag not in existing:
    existing.append(new_tag)
print(json.dumps({'tags': [{'uuid': u} for u in existing]}))
" "$tag_uuid")
    curl_post -X POST -d "$merged_tags" "$REST/location/${location_uuid}" >/dev/null \
      || fail "Failed to attach Login Location tag to '$name'"
    has_tag=$(location_already_has_tag "$location_uuid" "$tag_uuid" || true)
    if [ "$has_tag" = "yes" ]; then
      report "Tagged '$name' with Login Location"
      return 0
    fi
    merge_attempt=$((merge_attempt + 1))
    if [ "$merge_attempt" -lt "$merge_max" ]; then
      report "Tag attach for '$name' did not persist; retrying in 2s"
      sleep 2
    fi
  done
  fail "Tag attach for '$name' did not persist after ${merge_max} attempts"
}

# --- main --------------------------------------------------------------------

report "Seeding OpenMRS locations against $REST"

TAG_UUID=$(resolve_login_location_tag_uuid)
[ -n "$TAG_UUID" ] || fail "Could not resolve or create Login Location tag"

IFS=',' read -r -a SEED_NAMES <<< "$SEED_LOCATIONS_RAW"
for raw_name in "${SEED_NAMES[@]}"; do
  trimmed="$(echo "$raw_name" | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
  [ -n "$trimmed" ] || continue
  seed_one_location "$trimmed" "$TAG_UUID"
done

report "Done. ${#SEED_NAMES[@]} location(s) processed."
