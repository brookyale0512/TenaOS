#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-demo.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

QDRANT_URL="${QDRANT_URL:-http://localhost:${QDRANT_HOST_PORT:-6333}}"
SNAPSHOT_DIR_IN_CONTAINER="/qdrant/snapshots"

wait_for_qdrant() {
  local tries=60
  until curl -fsS "${QDRANT_URL}/readyz" >/dev/null 2>&1 || curl -fsS "${QDRANT_URL}/" >/dev/null 2>&1; do
    tries=$((tries - 1))
    if [[ "$tries" -le 0 ]]; then
      echo "Qdrant did not become ready at ${QDRANT_URL}" >&2
      exit 1
    fi
    sleep 2
  done
}

restore_collection() {
  local collection="$1"
  local snapshot="$2"
  echo "Restoring ${collection} from ${snapshot}"
  curl -fsS -X PUT "${QDRANT_URL}/collections/${collection}/snapshots/recover?wait=true" \
    -H "Content-Type: application/json" \
    -d "{\"location\":\"file://${SNAPSHOT_DIR_IN_CONTAINER}/${snapshot}\",\"priority\":\"snapshot\"}" >/dev/null
  echo "Restored ${collection}"
}

wait_for_qdrant
restore_collection "who_msf_guidelines" "who_msf_guidelines.snapshot"
restore_collection "ciel_concepts" "ciel_concepts.snapshot"

echo "Qdrant snapshots restored."
