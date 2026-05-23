#!/usr/bin/env bash
# TenaOS — start the bundled llama.cpp server (Gemma 4 E4B BF16 GGUF).
set -euo pipefail

MODEL="${TENAOS_LLM_GGUF:-/models/gemma-4-E4B-it-BF16.gguf}"
MMPROJ="${TENAOS_LLM_MMPROJ:-/models/mmproj-gemma-4-E4B-it-bf16.gguf}"
ALIAS="${TENAOS_LLM_MODEL:-gemma-4}"
CTX="${TENAOS_LLM_CTX_SIZE:-0}"

exec /opt/tenaos/llm/llama-server \
    -m "$MODEL" \
    --mmproj "$MMPROJ" \
    --host 127.0.0.1 \
    --port 8001 \
    -ngl 99 \
    --ctx-size "$CTX" \
    --jinja \
    --alias "$ALIAS" \
    --no-webui
