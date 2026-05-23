#!/usr/bin/env bash
# TenaOS — one-shot Qdrant snapshot restore.
#
# Supervised as a non-restarting program. On every container start:
#   1. Wait for Qdrant to be ready on localhost:6333.
#   2. For each *.snapshot file in /qdrant/snapshots/, check whether the
#      matching collection already exists with points > 0.
#   3. If the collection is empty/missing, POST the snapshot to Qdrant's
#      upload endpoint.
#   4. Exit non-zero if any expected snapshot failed to restore so the
#      operator sees the failure instead of silently running with an
#      empty knowledge base.
#
# The snapshot directory is bind-mounted from
# $TENAOS_QDRANT_SNAPSHOTS_PATH on the host (see docker-compose.yml).
# If the directory is empty or absent the restore is a deliberate no-op.
set -euo pipefail

QDRANT_URL="${TENAOS_QDRANT_URL:-http://127.0.0.1:6333}"
SNAPSHOT_DIR="/qdrant/snapshots"
STATUS_OK_MARKER="/opt/tenaos/runtime/qdrant-restore.ok"
STATUS_FAIL_MARKER="/opt/tenaos/runtime/qdrant-restore.failed"

log() { printf '[restore-qdrant] %s\n' "$*"; }

# Start the run with a clean slate so the healthcheck reflects this attempt.
rm -f "$STATUS_OK_MARKER" "$STATUS_FAIL_MARKER"
mkdir -p "$(dirname "$STATUS_OK_MARKER")"

mark_ok()   { date -u +%FT%TZ > "$STATUS_OK_MARKER"; }
mark_fail() { date -u +%FT%TZ > "$STATUS_FAIL_MARKER"; }

if [ ! -d "$SNAPSHOT_DIR" ] || ! compgen -G "$SNAPSHOT_DIR/*.snapshot" >/dev/null; then
  log "no snapshots at $SNAPSHOT_DIR — nothing to restore"
  mark_ok
  exit 0
fi

log "waiting for Qdrant at $QDRANT_URL ..."
for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 "$QDRANT_URL/readyz" >/dev/null 2>&1 \
     || curl -fsS --max-time 2 "$QDRANT_URL/" >/dev/null 2>&1; then
    log "Qdrant ready"
    break
  fi
  sleep 2
done
curl -fsS --max-time 2 "$QDRANT_URL/" >/dev/null \
  || { log "ERROR: Qdrant never became ready"; mark_fail; exit 1; }

FAILED_COLLECTIONS=()

for snap in "$SNAPSHOT_DIR"/*.snapshot; do
  collection="$(basename "$snap" .snapshot)"

  # Skip if the collection is already populated.
  points=$(
    curl -fsS --max-time 5 "$QDRANT_URL/collections/$collection" 2>/dev/null \
    | python3 -c "import json,sys;d=json.load(sys.stdin);print((d.get('result') or {}).get('points_count') or 0)" \
    2>/dev/null || echo 0
  )
  if [ "${points:-0}" -gt 0 ]; then
    log "$collection already has $points points — skipping restore"
    continue
  fi

  log "restoring $collection from $(basename "$snap") ..."
  # Let curl set Content-Type with the correct multipart boundary itself —
  # do NOT pass an explicit -H 'Content-Type: multipart/form-data'.
  http=$(
    curl -fsS -o /tmp/restore-$$.out -w "%{http_code}" \
      -X POST "$QDRANT_URL/collections/$collection/snapshots/upload" \
      -F "snapshot=@$snap" \
    || echo 000
  )
  if [ "$http" = "200" ] || [ "$http" = "201" ]; then
    log "  $collection OK (HTTP $http)"
  else
    log "  $collection FAILED (HTTP $http):"
    head -c 500 /tmp/restore-$$.out 2>/dev/null && echo
    FAILED_COLLECTIONS+=("$collection")
  fi
  rm -f /tmp/restore-$$.out
done

if [ "${#FAILED_COLLECTIONS[@]}" -gt 0 ]; then
  log "ERROR: failed to restore ${#FAILED_COLLECTIONS[@]} collection(s): ${FAILED_COLLECTIONS[*]}"
  log "Container is up but the AI agent will return zero-evidence results"
  log "for those collections. The container HEALTHCHECK will fail until"
  log "this marker is cleared:"
  log "  $STATUS_FAIL_MARKER"
  mark_fail
  exit 1
fi

mark_ok
log "restore pass complete"
