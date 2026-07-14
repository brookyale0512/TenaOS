#!/usr/bin/env bash
# TenaOS — start the bundled llama.cpp server. Serves the merged
# Gemma 4 E4B + task-tagged LoRA F16 GGUF by default (see
# https://huggingface.co/beza4588/TenaOS) so every deployment gets the
# adapter's task-tag routing ([form], [report], [scribe], [cds], [edu]),
# not the plain base model.
set -euo pipefail

# When TenaAgent is pointed at a remote backend (currently: a Vertex AI
# dedicated endpoint), the local GPU is not needed for inference. Stay up
# as a supervised no-op instead of loading the GGUF, so the GPU is free for
# other workloads (e.g. LoRA training) until switched back.
if [ "${TENAOS_LLM_BACKEND:-local}" != "local" ]; then
    echo "TENAOS_LLM_BACKEND=${TENAOS_LLM_BACKEND} -- skipping local llama-server (GPU stays free)."
    exec sleep infinity
fi

MODEL="${TENAOS_LLM_GGUF:-/models/tenaos-gemma-4-E4B-it-lora-F16.gguf}"
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
