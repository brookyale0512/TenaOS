# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] — 2026-05-23

First public release.

### Added
- **Single all-in-one Docker image** (`tenaos:latest`, ~23 GB). One `docker run`
  brings the entire stack up:
  - nginx (port 80) — frontend dist + reverse proxy
  - MariaDB 10.11 — OpenMRS DB
  - Tomcat + OpenMRS Ref-App 3 — system of record
  - Qdrant 1.15 — vector store (two collections)
  - llama.cpp — Gemma 4 E4B BF16 GGUF generation
  - `tena-agent` (Python) — agent orchestrator
  - `kb-guidelines` daemon — WHO/MSF retrieval (EmbedGemma in-process)
  - `kb-ciel` daemon — CIEL semantic search (EmbedGemma in-process)

  All eight processes supervised by `supervisord` on container localhost.
- `docker-compose.yml` — one service, named volumes, bind mounts for model
  artifacts.
- `scripts/fetch-models.sh` — placeholder for auto-downloading Gemma 4 GGUF,
  EmbedGemma 300M, and CIEL SQLite from the TenaOS HuggingFace organization
  (will activate once those repos are published).
- `TenaOS-KnowledgeBase/` package, deployed as two in-container daemons (one
  codebase, two collections) sharing one Qdrant.
- `TenaOS-CIEL/` package: 58,687-concept SQLite + FTS5 store for exact lookup,
  bundle expansion, and form-builder seed lists.
- `TenaOS-LLM/` ships a prebuilt llama.cpp `sm80/` binary (Ampere) and a
  Dockerfile that COPYs it into the image — no source compile at image build
  time.
- Apache 2.0 license, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.
- Per-component README following the same Purpose / Build / Run / Test /
  Environment shape.

### Changed
- `LLM_BACKEND` env layer collapsed: `TenaAgent` talks to a single
  OpenAI-compatible endpoint (`TenaOS-LLM`). The factory is a one-liner.
- Health endpoint key `vllm` → `llm`. The frontend and tests follow.
- Env var namespace consolidated to `TENAOS_*` (LLM, KB, CIEL, Qdrant) and
  `TENA_AGENT_*` (service-internal: port, CORS, tuning).
- Python package renamed: `cds_service` → `tena_agent_service`.
- Directory renames: `backend/` → `TenaOS-Backend/`, `frontend/` →
  `TenaOS-Frontend/`, `cds/` → `TenaAgent/`, `model_runtime/llama_cpp/` →
  `TenaOS-LLM/`.
- Docker image names follow OCI lowercase rules: `tenaos-backend:latest`,
  `tenaagent:latest`, `tenaos-frontend:latest`, `tenaos-llm:latest`,
  `tenaos-kb:latest`. Container names use the canonical brand casing.
- TenaAgent log lines and trace event names reference `llm` instead of `vllm`.

### Removed
- `LiteRT-LM` backend (`litert_lm.py`, all `TENA_AGENT_LITERT_*` env vars,
  the `.litertlm` model artifact).
- vLLM-specific guard process and launch path (`vllm_guard.py`,
  `count_vllm_processes`, `guard_before_launch`).
- DeepSeek-R1 / Vertex Garden client. Moved to the separate
  `/var/www/TenaOS_DeepSeek/` workspace for offline distillation.
- Entire `cds/gepa/` prompt-optimization stack. Moved to
  `/var/www/TenaOS_DeepSeek/gepa/`. The optimized prompt outputs (text files
  in `TenaAgent/service/tena_agent_service/prompts/optimized/`) stay so the
  production agent can use them.
- All `VLLM_*`, `VERTEX_*`, `CDS_*` legacy env-var aliases.
- Vendored `third_party/llama.cpp/` (replaced with pinned-tag build).
- Vendored `third_party/litert-lm/`.
- The 361 MB pre-built `tenaos-demo-bundle.zip` (publish via GitHub Releases).
- Personal documents (`resume_build/`), internal planning docs
  (`MIGRATION.md`, `VIDEO_SCRIPT.md`, `GEMMA4_GOOD_SUBMISSION_DRAFT.md`,
  `TENAAGENT_INTERNAL_RENAME.md`).
- Stale Q8 model artifact (`gemma-4-E4B-it-Q8_0.gguf`) — public release
  standardizes on BF16.
- Dual-backend swap scripts (`scripts/llama-test-swap.sh`,
  `scripts/llama-test-revert.sh`, `docker-compose.llama-test.yml`).
- The orphan `cds/service/cds/evals/` duplicate.
- Vertex service-account key from disk (`cds/runtime/vertex-key.json`).
