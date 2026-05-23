#!/usr/bin/env bash
# Build the WHO/MSF guidelines KB end-to-end: Docling JSON → chunks → enrichment →
# embeddings → Qdrant upsert. Idempotent — reruns skip anything already produced.
#
# Usage:
#   ./scripts/build_kb_guidelines.sh                # full build against $QDRANT_URL
#   ./scripts/build_kb_guidelines.sh --recreate     # drop & rebuild the collection
#   ./scripts/build_kb_guidelines.sh --dry-run      # validate alignment, no upload
#
# Env:
#   QDRANT_URL          default http://localhost:6333
#   QDRANT_API_KEY      optional
#   EMBEDGEMMA_PATH     default /home/RAZERBLADE/who_cds_pipeline/models/embedgemma-300m
#   WORKERS             chunking worker count (default 10)
#   EMBED_BATCH         encode batch size on A100 (default 512)

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")"/.. && pwd)"
SRC="$ROOT/pipeline/source"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"
QDRANT_API_KEY="${QDRANT_API_KEY:-}"
WORKERS="${WORKERS:-10}"
EMBED_BATCH="${EMBED_BATCH:-512}"
EMBEDGEMMA_PATH="${EMBEDGEMMA_PATH:-/home/RAZERBLADE/who_cds_pipeline/models/embedgemma-300m}"
export EMBEDGEMMA_PATH

RECREATE=""
DRY_RUN=""
for arg in "$@"; do
    case "$arg" in
        --recreate) RECREATE="--recreate" ;;
        --dry-run)  DRY_RUN="--dry-run" ;;
        *) echo "unknown flag: $arg" >&2; exit 2 ;;
    esac
done

step() { printf "\n\033[1;34m[%s] %s\033[0m\n" "$(date +%H:%M:%S)" "$*"; }

cd "$SRC"

step "Phase 1/7: Convert markdown -> Docling JSON"
python3 convert_md.py --workers "$WORKERS"

step "Phase 2/7: Classify docs"
python3 classify_docs.py

step "Phase 3/7: Fix heading levels"
python3 fix_heading_levels.py

step "Phase 4/7: Chunk docs"
python3 chunk_all.py --workers "$WORKERS"

step "Phase 5/7: Enrich + post-process + backfill"
python3 -m enrichment.tier1_metadata
python3 post_process_chunks.py
python3 backfill_metadata.py

step "Phase 6/7: Embed chunks on GPU"
mkdir -p embeddings_out
python3 embed_chunks_gpu.py \
    --chunks-dir chunks_output \
    --model-dir  "$EMBEDGEMMA_PATH" \
    --output-dir embeddings_out \
    --batch-size "$EMBED_BATCH"

step "Phase 7/7: Build Qdrant collection"
BUILD_ARGS=(--url "$QDRANT_URL")
[[ -n "$QDRANT_API_KEY" ]] && BUILD_ARGS+=(--api-key "$QDRANT_API_KEY")
[[ -n "$RECREATE" ]]       && BUILD_ARGS+=("$RECREATE")
[[ -n "$DRY_RUN" ]]        && BUILD_ARGS+=("$DRY_RUN")
python3 build_qdrant.py "${BUILD_ARGS[@]}"

step "Done."
