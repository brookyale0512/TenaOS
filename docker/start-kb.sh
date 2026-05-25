#!/usr/bin/env bash
# TenaOS — start a KB daemon (loads EmbedGemma in-process).
#   Usage: tenaos-start-kb <name> <port> <qdrant_collection>
set -euo pipefail

NAME="$1"
PORT="$2"
COLLECTION="$3"

log() { printf '[kb-%s] %s\n' "$NAME" "$*" >&2; }

log "Waiting for Qdrant at $TENAOS_QDRANT_URL ..."
for i in $(seq 1 60); do
  if curl -fsS --max-time 2 "$TENAOS_QDRANT_URL/collections" >/dev/null 2>&1; then
    log "Qdrant ready after ${i}*2s"; break
  fi
  sleep 2
done

log "Waiting for Qdrant collection '$COLLECTION' to be restored ..."
for i in $(seq 1 "${TENAOS_KB_COLLECTION_WAIT_ATTEMPTS:-180}"); do
  points="$(
    curl -fsS --max-time 5 "$TENAOS_QDRANT_URL/collections/$COLLECTION" 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("result") or {}).get("points_count") or 0)' \
      2>/dev/null || echo 0
  )"
  if [ "${points:-0}" -gt 0 ]; then
    log "Collection '$COLLECTION' ready with $points point(s) after ${i}*2s"
    break
  fi
  if [ "$i" -eq "${TENAOS_KB_COLLECTION_WAIT_ATTEMPTS:-180}" ]; then
    log "ERROR: collection '$COLLECTION' is missing or empty after waiting."
    log "Mount Qdrant snapshots and check qdrant-restore logs before starting KB services."
    exit 1
  fi
  sleep 2
done

export TENAOS_KB_HOST=127.0.0.1
export TENAOS_KB_PORT="$PORT"
export TENAOS_KB_COLLECTION="$COLLECTION"

cd /opt/tenaos/TenaOS-KnowledgeBase
exec python3 -m kb_guidelines.daemon
