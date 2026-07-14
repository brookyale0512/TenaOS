#!/usr/bin/env bash
# TenaOS — first-run artifact bootstrap.
#
# Downloads every host-side artifact the TenaOS image bind-mounts:
#   * Gemma 4 E4B + task-tagged LoRA, merged, F16 GGUF + mmproj  (~16 GB)
#   * EmbedGemma 300M                  (~1.2 GB)
#   * CIEL search SQLite               (~1.7 GB)
#   * WHO/MSF + CIEL Qdrant snapshots  (~0.8 GB)
#   * SapBERT (CIEL semantic encoder)  (~0.4 GB)
#
# TenaOS always serves the merged LoRA model (tenaos-gemma-4-E4B-it-lora-F16.gguf),
# never the plain base Gemma 4 E4B, so that every deployment gets the
# task-tagged adapter behavior described in the README and on the model
# card (https://huggingface.co/beza4588/TenaOS).
#
# Idempotent: any artifact that is already present on disk is skipped.
#
# Usage:
#   bash scripts/fetch-models.sh [<target_dir>]
#
# Override the upstream HuggingFace repos with env vars if you fork the
# artifacts to your own org:
#   TENAOS_HF_GEMMA_REPO    (model repo with both GGUF files)
#   TENAOS_HF_CIEL_REPO     (model repo with ciel_search.sqlite3)
#   TENAOS_HF_QDRANT_REPO   (model repo with *.snapshot files)
#   TENAOS_HF_EMBED_REPO    (EmbedGemma model repo — Google's by default)
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────
TARGET="${1:-$(pwd)/tenaos-bootstrap}"
GEMMA_REPO="${TENAOS_HF_GEMMA_REPO:-beza4588/TenaOS}"
CIEL_REPO="${TENAOS_HF_CIEL_REPO:-beza4588/tenaos-ciel-search-sqlite}"
QDRANT_REPO="${TENAOS_HF_QDRANT_REPO:-beza4588/tenaos-qdrant-snapshots}"
EMBED_REPO="${TENAOS_HF_EMBED_REPO:-google/embeddinggemma-300m}"
# SapBERT is the dense encoder the ciel_concepts collection was indexed with;
# the kb-ciel daemon must query with the same model. Override to self-host.
SAPBERT_REPO="${TENAOS_HF_SAPBERT_REPO:-cambridgeltl/SapBERT-from-PubMedBERT-fulltext}"

log() { printf '[fetch-models] %s\n' "$*"; }
die() { printf '[fetch-models] ERROR: %s\n' "$*" >&2; exit 1; }

# ── Tooling ──────────────────────────────────────────────────────────────
command -v curl >/dev/null || die "missing dependency: curl"

if ! command -v hf >/dev/null; then
  log "installing huggingface_hub (the 'hf' CLI) ..."
  python3 -m pip install --user --quiet --upgrade 'huggingface_hub>=1.0' \
    || die "failed to install huggingface_hub; install it manually and retry"
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v hf >/dev/null || die "'hf' CLI not on PATH after install; re-source your shell"

# Some upstream model repos (Gemma/EmbedGemma) may require accepting model
# terms. Run `hf auth login` first or export HF_TOKEN before this script if
# you hit an authorization error below.
if ! hf auth whoami >/dev/null 2>&1; then
  log "NOTE: not logged into HuggingFace."
  log "      If any download below returns 401, run 'hf auth login' or"
  log "      'export HF_TOKEN=<your token>' and re-run this script."
fi

# ── Layout ───────────────────────────────────────────────────────────────
MODELS_DIR="$TARGET/models"
EMBED_DIR="$TARGET/embedgemma-300m"
CIEL_DIR="$TARGET/ciel"
SNAPSHOTS_DIR="$TARGET/qdrant-snapshots"
SAPBERT_DIR="$TARGET/sapbert"
mkdir -p "$MODELS_DIR" "$EMBED_DIR" "$CIEL_DIR" "$SNAPSHOTS_DIR" "$SAPBERT_DIR"

# ── Helpers ──────────────────────────────────────────────────────────────
have_file() {
  # have_file <path> [<min_bytes>]
  local f="$1"
  local min="${2:-1}"
  [ -f "$f" ] && [ "$(stat -c %s "$f")" -ge "$min" ]
}

hf_download_file() {
  # hf_download_file <repo> <filename> <local_dir> [<repo_type>]
  local repo="$1" filename="$2" dest_dir="$3" repo_type="${4:-model}"
  log "  -> hf download $repo $filename ($repo_type) -> $dest_dir"
  hf download "$repo" "$filename" --local-dir "$dest_dir" --repo-type "$repo_type" >/dev/null
}

hf_download_dir() {
  # hf_download_dir <repo> <local_dir> [<repo_type>]
  local repo="$1" dest_dir="$2" repo_type="${3:-model}"
  log "  -> hf download $repo ($repo_type) -> $dest_dir"
  hf download "$repo" --local-dir "$dest_dir" --repo-type "$repo_type" >/dev/null
}

# ── 1. Gemma 4 E4B + LoRA merged F16 GGUF + mmproj projector ─────────────
# tenaos-gemma-4-E4B-it-lora-F16.gguf is the merged (base + task-tagged
# LoRA adapter) generation model — this is what TenaOS actually serves.
# The mmproj projector is unaffected by the LoRA merge (only attention/MLP
# projections were adapted), so it stays the base file; this matches the
# model card's own "Merged LoRA model" llama-server example exactly.
log "[1/5] Gemma 4 E4B + LoRA merged F16 GGUF (~16 GB) from hf.co/$GEMMA_REPO"
for f in tenaos-gemma-4-E4B-it-lora-F16.gguf mmproj-gemma-4-E4B-it-bf16.gguf; do
  if have_file "$MODELS_DIR/$f" 1000000; then
    log "      $f already present, skipping"
  else
    hf_download_file "$GEMMA_REPO" "$f" "$MODELS_DIR" "model"
    have_file "$MODELS_DIR/$f" 1000000 \
      || die "download produced no/empty file: $MODELS_DIR/$f"
  fi
done

# ── 2. EmbedGemma 300M ────────────────────────────────────────────────────
log "[2/5] EmbedGemma 300M (~1.2 GB) from hf.co/$EMBED_REPO"
if have_file "$EMBED_DIR/config.json" 100; then
  log "      EmbedGemma already present, skipping"
else
  hf_download_dir "$EMBED_REPO" "$EMBED_DIR" "model"
  have_file "$EMBED_DIR/config.json" 100 \
    || die "EmbedGemma config.json missing after download"
fi

# ── 3. CIEL search SQLite ────────────────────────────────────────────────
log "[3/5] CIEL search SQLite (~1.7 GB) from hf.co/$CIEL_REPO"
if have_file "$CIEL_DIR/ciel_search.sqlite3" 100000000; then
  log "      ciel_search.sqlite3 already present, skipping"
else
  hf_download_file "$CIEL_REPO" "ciel_search.sqlite3" "$CIEL_DIR" "model"
  have_file "$CIEL_DIR/ciel_search.sqlite3" 100000000 \
    || die "ciel_search.sqlite3 missing or suspiciously small after download"
fi

# ── 4. Qdrant snapshots (WHO/MSF guidelines + CIEL concepts) ─────────────
log "[4/5] Qdrant snapshots (~0.8 GB) from hf.co/$QDRANT_REPO"
for f in who_msf_guidelines.snapshot ciel_concepts.snapshot; do
  if have_file "$SNAPSHOTS_DIR/$f" 10000000; then
    log "      $f already present, skipping"
  else
    hf_download_file "$QDRANT_REPO" "$f" "$SNAPSHOTS_DIR" "model"
    have_file "$SNAPSHOTS_DIR/$f" 10000000 \
      || die "$f missing or suspiciously small after download"
  fi
done

# ── 5. SapBERT dense encoder (kb-ciel semantic query model) ──────────────
log "[5/5] SapBERT encoder (~0.4 GB) from hf.co/$SAPBERT_REPO"
if have_file "$SAPBERT_DIR/config.json" 100; then
  log "      SapBERT already present, skipping"
else
  hf_download_dir "$SAPBERT_REPO" "$SAPBERT_DIR" "model"
  have_file "$SAPBERT_DIR/config.json" 100 \
    || die "SapBERT config.json missing after download"
fi

# ── Done ─────────────────────────────────────────────────────────────────
log "All artifacts ready under: $TARGET"
log ""
log "Set these in your .env (alongside the OPENMRS_*_PASSWORD lines):"
log "  TENAOS_EMBED_MODEL_PATH=$EMBED_DIR"
log "  TENAOS_CIEL_SQLITE_PATH=$CIEL_DIR/ciel_search.sqlite3"
log "  TENAOS_QDRANT_SNAPSHOTS_PATH=$SNAPSHOTS_DIR"
log "  TENAOS_SAPBERT_PATH=$SAPBERT_DIR"
log ""
log "  TENAOS_MODELS_PATH=$MODELS_DIR"
log ""
log "Then: docker compose up -d"
