# TenaOS demo deployment

This is the operator runbook for bringing up the full TenaOS stack on a
single Linux host with a CUDA GPU.

For a one-paragraph overview, see [`README.md`](README.md). For the
contributor guide, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Prerequisites

- Linux host with an NVIDIA GPU, driver ≥ 535, Compute ≥ 8.0 (Ampere or newer).
- Docker ≥ 24.0 with the `nvidia-container-toolkit` plugin installed.
- ~30 GB free disk for the GGUF model + OpenMRS DB volume.
- Outbound HTTPS to HuggingFace once, to download the model files.

## Step 1 — Download model weights

Place these files into `./models/`:

```
models/gemma-4-E4B-it-BF16.gguf
models/mmproj-gemma-4-E4B-it-bf16.gguf
```

See [`models/README.md`](models/README.md) for the conversion command.

## Step 2 — Configure environment

```bash
cp demo.env.example .env
# Edit .env:
#   - rotate OPENMRS_DB_PASSWORD and OPENMRS_ADMIN_PASSWORD
#   - set TENAOS_PUBLIC_HOST to the public DNS name (or 'localhost')
```

## Step 3 — Launch

```bash
docker compose up -d
```

The first run takes about 10 minutes: OpenMRS bootstraps the MariaDB
schema, `TenaOS-LLM` builds llama.cpp from source, and Qdrant restores
its snapshots if you mounted them.

Monitor:

```bash
docker compose ps
docker compose logs -f tenaagent
```

## Step 4 — Verify

```bash
# OpenMRS REST
curl -fsS http://localhost:18080/openmrs/ws/rest/v1/session

# TenaAgent
curl -fsS http://localhost:8095/health | jq .

# TenaOS-LLM
curl -fsS http://localhost:8000/v1/models | jq .

# TenaOS-KnowledgeBase-Guidelines
curl -fsS http://localhost:4276/health | jq .

# TenaOS-KnowledgeBase-CIEL
curl -fsS http://localhost:4277/health | jq .
```

## Step 5 — Open the workspace

```text
http://<TENAOS_PUBLIC_HOST>:8080
```

Default OpenMRS admin credentials are whatever you set in
`OPENMRS_ADMIN_PASSWORD`. Change them on first login.

## TLS

The demo stack listens over HTTP. For any public deployment, terminate TLS
at a reverse proxy (nginx, Caddy, Traefik) in front of port 8080 and add
`HSTS` + `Strict-Transport-Security` headers. The bundled
`TenaOS-Frontend` nginx already sets the core security headers.

## Rolling restart of TenaAgent only

```bash
docker compose up -d --no-deps --force-recreate tenaagent
```

## Operational notes

- The `TenaAgent` runtime volume contains the SQLite stores for form
  drafts, report drafts, and trace logs. Back it up before any upgrade.
- The Qdrant volume is the source of truth for KB embeddings. If you
  rebuild from `./TenaOS-KnowledgeBase/pipeline/`, recreate the volume.
- `TenaOS-CIEL/ciel_search.sqlite3` is read-only at runtime. Regenerate
  offline from the latest CIEL release.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `tenaos-llm` exits with CUDA error | GPU compute capability mismatch — rebuild with `CMAKE_CUDA_ARCHITECTURES` matching your card |
| `tenaagent` /health returns `llm.healthy: false` | `TENAOS_LLM_URL` unreachable — check `docker compose logs tenaos-llm` |
| Empty KB results | Qdrant snapshots not restored — run `demo/restore-qdrant-snapshots.sh` |
| OpenMRS slow first boot | First run installs Ref-App modules — give it 5–10 minutes |
