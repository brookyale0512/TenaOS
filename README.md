<div align="center">

# TenaOS

An AI-native clinical operating system for primary-care clinics in
low- and middle-income countries — built on OpenMRS, powered by
Gemma 4 E4B, deployed as a single Docker image.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Gemma 4 E4B](https://img.shields.io/badge/Gemma_4_E4B-on--device-4285F4)](https://huggingface.co/google/gemma-4-E4B-it)
[![OpenMRS](https://img.shields.io/badge/OpenMRS-Ref--App_3-005f9c)](https://openmrs.org/)
[![Docker](https://img.shields.io/badge/Docker-single_image-2496ED?logo=docker&logoColor=white)](#quickstart)

</div>

---

TenaOS turns natural language into standards-based clinical data. A
clinical officer describes a workflow; the agent searches the CIEL
medical dictionary, plans the artifact, and produces a validated
OpenMRS form, scribe note, decision-support recommendation, patient
handout, or FHIR report. Every clinical change is a draft a human
approves.

**Gemma 4 E4B proposes. Middleware verifies. Clinicians approve.**

## Architecture

One Docker image. Eight processes inside, supervised on container
localhost. Nothing leaves the container except through `:80`.

```mermaid
flowchart LR
    Clinician(["Clinician"])

    subgraph Container ["tenaos:latest"]
        direction TB
        Nginx["nginx :80"]

        subgraph App ["Application"]
            direction LR
            Agent["TenaAgent :8095"]
            OMRS["OpenMRS :8080"]
        end

        subgraph AI ["On-device AI"]
            direction LR
            LLM["llama.cpp :8001<br/>Gemma 4 E4B BF16"]
            KBG["kb-guidelines :4276<br/>WHO + MSF"]
            KBC["kb-ciel :4277<br/>CIEL semantic"]
        end

        subgraph Data ["State"]
            direction LR
            DB[("MariaDB")]
            QD[("Qdrant")]
            CIEL[("CIEL SQLite")]
        end

        Nginx --> Agent
        Nginx --> OMRS
        Agent --> OMRS
        Agent --> LLM
        Agent --> KBG
        Agent --> KBC
        Agent --> CIEL
        OMRS --> DB
        KBG --> QD
        KBC --> QD
    end

    Clinician -->|"HTTPS"| Nginx
```

Both knowledge-base daemons load **EmbedGemma 300M** in-process and
share one Qdrant for hybrid (dense + BM25) retrieval. Model weights,
EmbedGemma, and the CIEL SQLite are bind-mounted from the host.

## Quickstart

### Prerequisites

| | Minimum | Recommended |
|---|---|---|
| **OS** | Linux x86-64 | Ubuntu 22.04+ |
| **GPU** | NVIDIA, compute ≥ 8.0 (Ampere) | A100 / RTX 40-series |
| **GPU VRAM** | 16 GB (BF16 Gemma 4 E4B fits comfortably) | 24 GB+ for headroom |
| **System RAM** | 16 GB | 32 GB |
| **Disk** | 30 GB free (weights + image + DB) | 100 GB |
| **Docker** | 24.0+ with `nvidia-container-toolkit` | latest |

### Steps

```bash
# Fetch artifacts, generate .env, validate GPU/port/passwords, and launch.
bash scripts/setup-demo.sh
```

The setup wrapper validates Docker Compose, NVIDIA GPU visibility, host
port availability, OpenMRS password policy, and all required artifact
paths before it starts the container.

The Qdrant knowledge-base collections (`who_msf_guidelines` +
`ciel_concepts`) restore automatically from the downloaded snapshots
on first container boot.

If port `8080` is already in use, choose another port:

```bash
bash scripts/setup-demo.sh --port 28061
```

If you prefer the manual path, run `bash scripts/fetch-models.sh`, copy
`demo.env.example` to `.env`, paste the printed artifact paths, rotate
the `OPENMRS_*_PASSWORD` values, then run `docker compose up -d`.

### Artifact source repositories

| Artifact | HuggingFace repo | Visibility |
| --- | --- | --- |
| Gemma 4 E4B BF16 GGUF + mmproj | [`beza4588/TenaOS`](https://huggingface.co/beza4588/TenaOS) | public |
| EmbedGemma 300M | [`google/embeddinggemma-300m`](https://huggingface.co/google/embeddinggemma-300m) | public (Gemma terms) |
| CIEL search SQLite | [`beza4588/tenaos-ciel-search-sqlite`](https://huggingface.co/beza4588/tenaos-ciel-search-sqlite) | public model repo |
| Qdrant snapshots (WHO/MSF + CIEL) | [`beza4588/tenaos-qdrant-snapshots`](https://huggingface.co/beza4588/tenaos-qdrant-snapshots) | public model repo |

If `fetch-models.sh` returns an authorization error for a gated model,
run `hf auth login` (or `export HF_TOKEN=<your token>`) and re-run the
script. The script is idempotent.

To self-host the artifacts on your own HuggingFace org, override
`TENAOS_HF_GEMMA_REPO`, `TENAOS_HF_CIEL_REPO`, `TENAOS_HF_QDRANT_REPO`,
or `TENAOS_HF_EMBED_REPO` before running `fetch-models.sh`.

## Capabilities

| | |
|---|---|
| **Form builder** | Natural-language → CIEL-validated OpenMRS forms |
| **AI scribe** | Voice or text → SOAP note + coded observations |
| **Decision support** | Evidence-grounded recommendations from WHO + MSF guidelines |
| **Patient material** | Plain-language education drafts in the patient's language |
| **Report builder** | Plain-language questions → deterministic FHIR query plans |

## Components

| Directory | Role |
|---|---|
| [`TenaOS-Frontend/`](TenaOS-Frontend/) | React + Vite clinical workspace |
| [`TenaOS-Backend/`](TenaOS-Backend/) | OpenMRS Reference Application 3 distribution |
| [`TenaAgent/`](TenaAgent/) | Python agent service — prompts, tool loops, OpenMRS writers |
| [`TenaOS-LLM/`](TenaOS-LLM/) | `llama.cpp` CUDA server (Gemma 4 E4B BF16 GGUF) |
| [`TenaOS-KnowledgeBase/`](TenaOS-KnowledgeBase/) | Qdrant + EmbedGemma retrieval daemon |
| [`TenaOS-CIEL/`](TenaOS-CIEL/) | CIEL terminology — SQLite + FTS5 |
| [`docker/`](docker/) | `supervisord`, internal nginx, start scripts |
| [`models/`](models/) | Bind-mounted GGUF weights (gitignored) |

Each top-level component has a README with the same
**Purpose / Build / Run / Test / Environment** shape.

## Models

| Component | Model |
|---|---|
| Generation | [`google/gemma-4-E4B-it`](https://huggingface.co/google/gemma-4-E4B-it) — BF16 GGUF |
| Embeddings | [`google/embeddinggemma-300m`](https://huggingface.co/google/embeddinggemma-300m) |

Standardized on BF16 full precision. Native audio rides on Gemma 4's
`mmproj` projector through `llama.cpp`.

## Safety boundary

TenaOS does not treat the model as an authority.

- Tool calls are allow-listed; the model can only do what the
  middleware exposes.
- Medical concepts are validated locally against CIEL before any write.
- Final writes go through OpenMRS, never directly through the model.
- Recommendations cite retrieved evidence; unsupported answers do not
  appear.
- Every reasoning trace and tool call is auditable in the UI.

The agent never writes to OpenMRS directly. Every clinical change is a
draft a human approves.

## Status

TenaOS is a research and challenge-submission codebase. It is the live
software behind [demo.tenaos.com](https://demo.tenaos.com).

It is not a HIPAA-regulated product, not a CE-marked or FDA-cleared
medical device, and not safety-of-life software. Operators deploying
TenaOS in real clinical settings remain responsible for local
regulatory compliance and clinical risk management.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).

## Acknowledgments

Built on
[OpenMRS](https://openmrs.org/),
[Gemma 4](https://ai.google.dev/gemma) and
[EmbedGemma](https://huggingface.co/google/embeddinggemma-300m),
[llama.cpp](https://github.com/ggerganov/llama.cpp),
[Qdrant](https://qdrant.tech/),
[CIEL](https://openconceptlab.org/orgs/CIEL),
[WHO SMART Guidelines](https://www.who.int/teams/digital-health-and-innovation/smart-guidelines),
and the [MSF clinical guidelines](https://medicalguidelines.msf.org/).
