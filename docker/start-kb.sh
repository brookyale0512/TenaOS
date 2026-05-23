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

export TENAOS_KB_HOST=0.0.0.0
export TENAOS_KB_PORT="$PORT"
export TENAOS_KB_COLLECTION="$COLLECTION"

cd /opt/tenaos/TenaOS-KnowledgeBase
exec python3 -m kb_guidelines.daemon
