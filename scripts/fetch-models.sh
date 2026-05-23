#!/usr/bin/env bash
# TenaOS — first-run model bootstrap.
#
# Downloads everything the TenaOS image needs to run, from the official
# TenaOS HuggingFace organization. Idempotent: skips files that already
# exist.
#
# Usage:
#   bash scripts/fetch-models.sh [<target_dir>]
#
# Files fetched into <target_dir> (default: ./tenaos-models):
#   models/gemma-4-E4B-it-BF16.gguf            (~16 GB)
#   models/mmproj-gemma-4-E4B-it-bf16.gguf     (~0.5 GB)
#   embedgemma-300m/                            (~1.2 GB)
#   ciel/ciel_search.sqlite3                    (~1.7 GB)
#
# After this completes, point your .env at the downloaded paths:
#   TENAOS_EMBED_MODEL_PATH=<target_dir>/embedgemma-300m
#   TENAOS_CIEL_SQLITE_PATH=<target_dir>/ciel/ciel_search.sqlite3
#
# Then `docker compose up -d`.
set -euo pipefail

TARGET="${1:-$(pwd)/tenaos-models}"
HF_ORG="${TENAOS_HF_ORG:-tenaos}"

# Public release artifacts. Adjust repo names once published.
GEMMA_REPO="${TENAOS_HF_GEMMA_REPO:-tenaos/gemma-4-E4B-it-gguf}"
EMBED_REPO="${TENAOS_HF_EMBED_REPO:-google/embeddinggemma-300m}"
CIEL_REPO="${TENAOS_HF_CIEL_REPO:-tenaos/ciel-search-sqlite}"

mkdir -p "$TARGET/models" "$TARGET/embedgemma-300m" "$TARGET/ciel"

need() { command -v "$1" >/dev/null || { echo "Missing dependency: $1" >&2; exit 1; }; }
need curl
if ! command -v huggingface-cli >/dev/null; then
  echo "Installing huggingface_hub ..."
  pip install --user huggingface_hub
  export PATH="$HOME/.local/bin:$PATH"
fi

log() { printf '[fetch-models] %s\n' "$*"; }

# ── Gemma 4 BF16 GGUF + multimodal projector ─────────────────────────────
for f in gemma-4-E4B-it-BF16.gguf mmproj-gemma-4-E4B-it-bf16.gguf; do
  if [ ! -f "$TARGET/models/$f" ]; then
    log "Downloading $f from hf.co/$GEMMA_REPO ..."
    huggingface-cli download "$GEMMA_REPO" "$f" --local-dir "$TARGET/models"
  else
    log "Skipping $f (already present)"
  fi
done

# ── EmbedGemma 300M ──────────────────────────────────────────────────────
if [ ! -f "$TARGET/embedgemma-300m/config.json" ]; then
  log "Downloading EmbedGemma 300M from hf.co/$EMBED_REPO ..."
  huggingface-cli download "$EMBED_REPO" --local-dir "$TARGET/embedgemma-300m"
else
  log "Skipping EmbedGemma (already present)"
fi

# ── CIEL search SQLite ───────────────────────────────────────────────────
if [ ! -f "$TARGET/ciel/ciel_search.sqlite3" ]; then
  log "Downloading CIEL search SQLite from hf.co/$CIEL_REPO ..."
  huggingface-cli download "$CIEL_REPO" ciel_search.sqlite3 --local-dir "$TARGET/ciel"
else
  log "Skipping CIEL SQLite (already present)"
fi

log "Done."
log "Update .env:"
log "  TENAOS_EMBED_MODEL_PATH=$TARGET/embedgemma-300m"
log "  TENAOS_CIEL_SQLITE_PATH=$TARGET/ciel/ciel_search.sqlite3"
log "and place the GGUF files alongside the image: ln -sf $TARGET/models <repo>/models"
