# TenaOS CDS Middleware

This service provides the patient AI insight flow. It keeps model calls and DAK execution outside the browser.

## Run Locally

```bash
cd /var/www/tenaos/cds/service
python -m pip install -r requirements.txt
python main.py
```

Defaults:

- CDS service: `http://localhost:8095`
- vLLM OpenAI-compatible endpoint: `http://localhost:8000/v1`
- Model name: `gemma-4`
- OpenMRS REST: `http://localhost:18080/openmrs/ws/rest/v1`
- OpenMRS FHIR R4: `http://localhost:18080/openmrs/ws/fhir2/R4`

## vLLM Guard

Before launching a local vLLM instance, run:

```bash
python vllm_guard.py
```

The guard reuses a healthy endpoint, refuses to launch when a vLLM-like process is already present but unhealthy, and only starts `VLLM_LAUNCH_COMMAND` when no endpoint/process exists.

## Safety Boundary

Gemma 4 only selects simplified tools and formats final text. The deterministic DAK executor owns rule matching and missing-data behavior.
