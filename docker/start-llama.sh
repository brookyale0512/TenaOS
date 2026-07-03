#!/usr/bin/env bash
# TenaOS — start the bundled llama.cpp server.
#
# TEMPORARY: normally serves the merged Gemma 4 E4B + task-tagged LoRA F16
# GGUF by default (see https://huggingface.co/beza4588/TenaOS) so every
# deployment gets the adapter's task-tag routing ([form], [report], [scribe],
# [cds], [edu]). The published adapter is currently being retrained to fix a
# data/production parity gap, so this build serves the plain base model
# instead until the new adapter is validated and republished — revert the
# MODEL default below to tenaos-gemma-4-E4B-it-lora-F16.gguf once that lands.
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
