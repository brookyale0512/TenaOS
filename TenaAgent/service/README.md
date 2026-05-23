# TenaAgent service

Python service that owns every LLM-mediated workflow in TenaOS. See
[`TenaAgent/README.md`](../README.md) for the full component overview.

## Module map

| Module | Role |
| --- | --- |
| [`tena_agent_service/app.py`](tena_agent_service/app.py)             | HTTP server, route dispatch, health endpoint |
| [`tena_agent_service/llm_client.py`](tena_agent_service/llm_client.py) | OpenAI-compatible client for `TenaOS-LLM` |
| [`tena_agent_service/llm_backend.py`](tena_agent_service/llm_backend.py) | One-line factory returning `LlmClient` |
| [`tena_agent_service/tool_loop.py`](tena_agent_service/tool_loop.py)   | Clinical decision support reasoning loop |
| [`tena_agent_service/material_loop.py`](tena_agent_service/material_loop.py) | Patient-education material loop |
| [`tena_agent_service/form_conversation.py`](tena_agent_service/form_conversation.py) | Form-builder conversation driver |
| [`tena_agent_service/form_builder_tool_loop.py`](tena_agent_service/form_builder_tool_loop.py) | Form-builder tool-call loop |
| [`tena_agent_service/report_conversation.py`](tena_agent_service/report_conversation.py) | Report-builder conversation driver |
| [`tena_agent_service/scribe_tool_loop.py`](tena_agent_service/scribe_tool_loop.py) | SOAP scribe loop |
| [`tena_agent_service/ciel.py`](tena_agent_service/ciel.py)            | CIEL terminology client |
| [`tena_agent_service/openmrs_writer.py`](tena_agent_service/openmrs_writer.py) | The single, audited path that writes to OpenMRS |
| [`tena_agent_service/openmrs_reader.py`](tena_agent_service/openmrs_reader.py) | OpenMRS REST + FHIR read paths |
| [`tena_agent_service/insight_traces.py`](tena_agent_service/insight_traces.py) | Reasoning-trace persistence |

## Develop

```bash
pip install -r requirements.txt
TENAOS_LLM_URL=http://localhost:8001/v1 \
TENAOS_CIEL_ROOT=/var/www/TenaOS/TenaOS-CIEL \
TENA_AGENT_SERVICE_HOST=127.0.0.1 \
python main.py
```

Direct TenaAgent access is intended for local development only.
TenaAgent does not yet provide its own API authentication layer, so do
not publish port `8095` to untrusted networks. Production all-in-one
deployments keep TenaAgent behind nginx and expose it only through
`/agent-api`.

## Test

```bash
python -m pytest
```
