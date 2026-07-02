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

## Security boundary

The KB HTTP daemon binds to `127.0.0.1` by default. Set
`TENAOS_KB_HOST=0.0.0.0` only behind an authentication proxy or private
container network. Set `TENAOS_KB_SHARED_SECRET` to require callers to
send the same value in `X-TenaOS-KB-Secret`.

Qdrant has no API key. Inside the single TenaOS image it binds to
**`127.0.0.1` only** (see [`docker/start-llama.sh`](../docker/start-llama.sh)
and the supervised `qdrant` process in
[`docker/supervisord.conf`](../docker/supervisord.conf)) — meaning only
processes inside the same container can talk to it. Never expose the
Qdrant port (`6333`) on the host network without first putting an
authentication proxy in front of it.

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TENAOS_KB_HOST`        | `127.0.0.1`           | Listen interface |
| `TENAOS_KB_PORT`        | `4276`                | Listen port |
| `TENAOS_KB_COLLECTION`  | `who_msf_guidelines`  | Qdrant collection name |
| `TENAOS_KB_MAX_BODY_BYTES` | `4194304`          | Maximum HTTP request body |
| `TENAOS_KB_SHARED_SECRET` | _(unset)_           | Optional shared-secret header |
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
