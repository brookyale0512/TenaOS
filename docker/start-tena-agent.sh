#!/usr/bin/env bash
# TenaOS — start TenaAgent. Waits for the OpenMRS REST endpoint so the
# first /health probe reports a fully wired stack.
set -euo pipefail

log() { printf '[tena-agent] %s\n' "$*" >&2; }

log "Waiting for OpenMRS REST endpoint at $OPENMRS_REST_BASE_URL ..."
for i in $(seq 1 120); do
  if curl -fsS --max-time 3 "$OPENMRS_REST_BASE_URL/session" >/dev/null 2>&1; then
    log "OpenMRS ready after ${i}*5s"; break
  fi
  sleep 5
done

cd /opt/tenaos/TenaAgent/service
export RAINDROP_LOCAL_DEBUGGER="${RAINDROP_LOCAL_DEBUGGER:-http://host.docker.internal:8086/v1/}"
export FORM_AGENT_SUBJECT_ASSESSMENT="${FORM_AGENT_SUBJECT_ASSESSMENT:-1}"
exec python3 main.py
