# TenaOS-KnowledgeBase

Hybrid (dense + BM25) retrieval over Qdrant. EmbedGemma-300M generates
dense vectors; BM25 sparse vectors are computed in-process. The daemon
exposes a tiny HTTP surface that TenaAgent calls during clinical
decision support and patient material generation.

## Purpose

A single codebase, deployed as **two containers** in `docker-compose.yml`:

| Container | Default port | Qdrant collection | Purpose |
| --- | --- | --- | --- |
| `TenaOS-KnowledgeBase-Guidelines` | 4276 | `who_msf_guidelines` | WHO and MSF clinical guidelines |
| `TenaOS-KnowledgeBase-CIEL`       | 4277 | `ciel_concepts`      | CIEL concept semantic search |

Both containers share the same image and EmbedGemma weights. They differ
only by `TENAOS_KB_COLLECTION` and `TENAOS_KB_PORT`.

## Build

```bash
docker build -t tenaos-kb:latest .
```

## Run (local)

```bash
TENAOS_KB_COLLECTION=who_msf_guidelines \
TENAOS_KB_PORT=4276 \
TENAOS_QDRANT_URL=http://localhost:6333 \
python3 -m kb_guidelines.daemon
```

## Test

```bash
curl -fsS http://localhost:4276/health
curl -fsS -X POST http://localhost:4276/search \
  -H 'Content-Type: application/json' \
  -d '{"query": "uncomplicated malaria first-line treatment", "k": 5}'
```

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TENAOS_KB_HOST`        | `0.0.0.0`             | Listen interface |
| `TENAOS_KB_PORT`        | `4276`                | Listen port |
| `TENAOS_KB_COLLECTION`  | `who_msf_guidelines`  | Qdrant collection name |
| `TENAOS_QDRANT_URL`     | `http://tenaos-qdrant:6333` | Qdrant endpoint |
| `EMBEDGEMMA_PATH`       | _(model cache)_       | Local EmbedGemma-300M snapshot |
| `KB_BM25_CACHE`         | _(next to module)_    | JSON cache for sparse stats |

## Layout

```
TenaOS-KnowledgeBase/
├── Dockerfile
├── requirements.txt
├── kb_guidelines/        HTTP daemon + retrieval core
├── kb_common/            BM25 sparse encoder (shared utility)
├── pipeline/             Offline corpus → Qdrant indexing pipeline
└── scripts/              Build helpers
```
