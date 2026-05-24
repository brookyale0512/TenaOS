# TenaAgent

The AI orchestration service inside TenaOS. TenaAgent owns every
LLM-mediated workflow — form authoring, decision support, patient
education, SOAP scribe, report writing — and is the *only* component
that talks to the model server.

The agent **never** writes to OpenMRS directly. Every clinical change
goes through `OpenmrsWriter` as a draft that a clinician approves in the
UI.

## Purpose

| Workflow | Module |
| --- | --- |
| Natural-language OpenMRS form builder | `form_conversation.py`, `form_builder_tool_loop.py` |
| Clinical decision support              | `tool_loop.py` |
| Patient-education material generation  | `material_loop.py` |
| Plain-language report generation       | `report_conversation.py`, `report_builder_tool_loop.py` |
| SOAP scribe and note extraction        | `scribe.py`, `scribe_tool_loop.py` |
| Lab catalog lookup                     | `lab_catalog.py` |

## Build

```bash
docker build -t tenaagent:latest -f TenaAgent/service/Dockerfile .
```

## Run (local, no container)

```bash
cd TenaAgent/service
pip install -r requirements.txt
TENAOS_LLM_URL=http://localhost:8000/v1 \
TENAOS_CIEL_ROOT=/var/www/TenaOS/TenaOS-CIEL \
TENA_AGENT_SERVICE_HOST=127.0.0.1 \
TENA_AGENT_SERVICE_PORT=8095 \
python main.py
```

Direct TenaAgent access is for local development only. TenaAgent does
not yet enforce its own API authentication layer, so do not expose
`:8095` to untrusted networks. Production all-in-one deployments keep
TenaAgent on loopback behind nginx and expose it only through
`/agent-api`.

## Test

```bash
cd TenaAgent/service
python -m pytest
```

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `TENAOS_LLM_URL`            | `http://localhost:8001/v1`   | OpenAI-compatible endpoint of `TenaOS-LLM` |
| `TENAOS_LLM_MODEL`          | `gemma-4`                    | Model alias as configured in llama-server |
| `TENAOS_LLM_API_KEY`        | `EMPTY`                      | Bearer token (llama-server ignores) |
| `TENAOS_KB_GUIDELINES_URL`  | `http://localhost:4276`      | WHO/MSF guideline retrieval |
| `TENAOS_KB_CIEL_URL`        | `http://localhost:4277`      | CIEL semantic search |
| `TENAOS_CIEL_ROOT`          | `../TenaOS-CIEL`             | Path to `TenaOS-CIEL/` |
| `TENAOS_CIEL_SQLITE`        | `<root>/ciel_search.sqlite3` | CIEL store path |
| `OPENMRS_REST_BASE_URL`     | `http://localhost:18080/openmrs/ws/rest/v1` | OpenMRS REST endpoint |
| `OPENMRS_FHIR_BASE_URL`     | `http://localhost:18080/openmrs/ws/fhir2/R4` | OpenMRS FHIR endpoint |
| `TENA_AGENT_SERVICE_HOST`   | `0.0.0.0`                    | TenaAgent listen host; use `127.0.0.1` for direct local runs |
| `TENA_AGENT_SERVICE_PORT`   | `8095`                       | TenaAgent listen port |
| `TENA_AGENT_CORS_ORIGINS`   | `http://localhost:3000,http://localhost:5173` | Allowed browser origins |
| `TENAOS_USE_OPTIMIZED_PROMPTS` | _unset_                   | Use GEPA-optimized prompt overlay if present |

## Layout

```
TenaAgent/
├── service/
│   ├── Dockerfile
│   ├── main.py
│   ├── requirements.txt
│   ├── tena_agent_service/   Python package: prompts, tool loops, writers, drafts
│   └── tests/
├── sources/                  WHO SMART submodules (read-only)
└── evals/                    In-repo eval harnesses (form builder, KB, CIEL)
```
