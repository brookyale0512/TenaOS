#!/usr/bin/env bash
# TenaOS — one-shot Qdrant snapshot restore.
#
# Supervised as a non-restarting program. On every container start:
#   1. Wait for Qdrant to be ready on localhost:6333.
#   2. For each *.snapshot file in /qdrant/snapshots/, check whether the
#      matching collection already exists with points > 0.
#   3. If the collection is empty/missing, POST the snapshot to Qdrant's
#      upload endpoint.
#   4. Exit 0 either way; supervisord won't restart this program.
#
# The snapshot directory is bind-mounted from
# $TENAOS_QDRANT_SNAPSHOTS_PATH on the host (see docker-compose.yml).
# If the directory is empty or absent the restore is a no-op.
set -euo pipefail

QDRANT_URL="${TENAOS_QDRANT_URL:-http://127.0.0.1:6333}"
SNAPSHOT_DIR="/qdrant/snapshots"

log() { printf '[restore-qdrant] %s\n' "$*"; }

if [ ! -d "$SNAPSHOT_DIR" ] || ! compgen -G "$SNAPSHOT_DIR/*.snapshot" >/dev/null; then
  log "no snapshots at $SNAPSHOT_DIR — nothing to restore"
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
  || { log "ERROR: Qdrant never became ready"; exit 1; }

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
  http=$(
    curl -fsS -o /tmp/restore-$$.out -w "%{http_code}" \
      -X POST "$QDRANT_URL/collections/$collection/snapshots/upload" \
      -H "Content-Type: multipart/form-data" \
      -F "snapshot=@$snap" \
    || echo 000
  )
  if [ "$http" = "200" ] || [ "$http" = "201" ]; then
    log "  $collection OK (HTTP $http)"
  else
    log "  $collection FAILED (HTTP $http):"
    head -c 500 /tmp/restore-$$.out 2>/dev/null && echo
  fi
  rm -f /tmp/restore-$$.out
done

log "restore pass complete"
