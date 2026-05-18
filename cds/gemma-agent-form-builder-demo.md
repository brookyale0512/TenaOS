# Gemma 4 Agent Form Builder Demo

This demo shows the form builder as a Gemma 4-native agentic workflow. Gemma
plans the form, calls allow-listed tools, searches and reviews CIEL results,
updates the draft basket, and asks the user to review before publishing.

## Architecture

1. User asks for a form, for example: `Create an ENT intake form`.
2. Gemma 4 performs a brainstorm turn at `temperature=0.3`.
3. Gemma 4 enters the tool loop at `temperature=0.0`.
4. Gemma calls tools from `FORM_TOOL_SCHEMAS`:
   - `get_form_draft`
   - `search_ciel_seeds`
   - `expand_ciel_concept`
   - `update_form_draft`
   - `build_form_schema`
5. Middleware executes only allow-listed tools and validates all changes.
6. The UI shows the final draft by default.
7. The `Show reasoning` toggle exposes brainstorms, tool calls, CIEL results,
   accept/refine/drop decisions, and schema build results.

The model never writes OpenMRS schema JSON directly. OpenMRS schema generation
stays deterministic through the basket-to-schema builder so the final artifact is
reproducible and validated.

## Required Services

- CDS service: `http://127.0.0.1:8095`
- Gemma 4 vLLM endpoint: `http://127.0.0.1:8000/v1`
- CIEL SQLite knowledge base: configured by `CDS_CIEL_REPO_ROOT`
- OpenMRS REST: `http://127.0.0.1:18080/openmrs/ws/rest/v1`

Check:

```bash
curl http://127.0.0.1:8095/health
curl http://127.0.0.1:8000/v1/models
```

The CDS health response should show `vllm.healthy=true` and
`ciel.available=true`.

## Demo Script

1. Open `/forms/new`.
2. Enter: `Create an ENT intake form`.
3. Pick `Consultation` as the encounter type.
4. Wait for Gemma to build the draft.
5. Click `Show reasoning`.
6. Point out:
   - the brainstorm model call at `temperature=0.3`
   - CIEL search tool calls
   - CIEL candidate results
   - Gemma accept/refine/drop behavior
   - `update_form_draft` calls
   - `build_form_schema`
7. Review the preview and basket.
8. Publish the form.
9. Open a patient with an active visit.
10. Fill the form from the Forms tab.
11. Save the form.
12. Confirm it appears in:
    - Timeline under `Forms`
    - Forms tab with expandable recorded answers

## Safety Boundaries

- Gemma chooses tool calls and arguments.
- Middleware executes only allow-listed tools.
- CIEL concepts are validated before they enter the basket.
- `publish_form` remains blocked until the user confirms publish.
- `basket_to_schema` and `validate_schema` remain deterministic.
