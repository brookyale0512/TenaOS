# Agent Operator Contract

This document describes the runtime contract for the clinic setup agent implemented by `agent/runtime.py`, the CLI wrapper in `agent/start_agent.py`, and the MCP/control-plane tools in `agent/emr_mcp_server.py`.

## State Model

The hardened agent now treats clinic state as three separate layers:

- `active`
  - The live applied clinic configuration currently recorded in control-plane state.
  - Read through `get_current_config` and other read tools with `state="active"`.
  - `get_current_config` is read-only and does not mutate the working copy.
- `working`
  - The session-local staged configuration persisted per `clinic_id` in `data/sessions/<clinic_id>.json`.
  - Auto-seeded from the active config on the first write when a live config exists.
  - If a staged working copy is structurally corrupted while a valid live config exists, the MCP layer now auto-recovers the working copy back from `active`, clears any stale pending bundle, and records a recovery note.
  - Queried through read tools with `state="working"`.
- `pending`
  - The previewed but not yet applied bundle tracked as `pending_bundle_id` in the same session file.
  - `preview_change` sets this state and flips on an explicit confirmation gate.

`get_current_config` now distinguishes these startup cases:

- `ACTIVE_CONFIG_LOADED`
  - A live clinic is already applied.
- `WORKING_CONFIG_STAGED`
  - No live clinic is applied yet, but this session already has staged working data.
  - The agent must not call `compose_from_packs` again in this state.
- `NO_ACTIVE_CONFIG`
  - No live clinic and no staged working clinic exist yet.

## Required Tool Flow

The runtime contract is now:

1. `get_current_config`
2. If `status="NO_ACTIVE_CONFIG"`: `compose_from_packs`
3. Read-only questions:
   - `get_change_status`
   - `get_clinic_inventory`
   - `get_room_forms`
   - `get_department_flow`
   - `get_form_details`
   - `analyze_patient_flow`
4. Write path:
   - `search_ciel` when a clinical concept is involved
   - edit tools such as `create_form`, `add_form_section`, `add_form_question`, `add_location`, or `patch_config`
   - `validate_config`
   - `preview_change`
   - `ask_user` for explicit approval
   - `apply_change({"confirmed": true})`

The agent must not auto-apply immediately after previewing.
When `apply_change({"confirmed": true})` is called after explicit user approval, the MCP layer now records a control-plane approval entry before invoking the backend apply path. This keeps the runtime governance check aligned with the conversational approval step.

## Read/Write Guardrails

- `compose_from_packs` now refuses to overwrite an already loaded working copy or an already active clinic binding.
- `get_current_config` no longer reports a staged working clinic as plain `NO_ACTIVE_CONFIG`.
- `patch_config(remove)` now refuses to delete an entire top-level collection such as `locations`, `forms`, or `queues`; callers must target a specific item instead.
- `add_form_section` and `add_form_question` are idempotent by section/question ID and return `already_exists` instead of appending duplicates.
- `create_form` now provides an explicit path for new dedicated forms, so requests for a separate form no longer have to fall back to mutating `triage-form`.
- `get_department_flow` now answers department-specific routing questions without pretending a whole-clinic flow is a dedicated pediatric/ENT flow.
- `apply_change` now hard-blocks when the user has not explicitly approved the previewed bundle.
- After explicit approval, `apply_change` also satisfies the backend approval ledger expected by `lmic_emr_os.runtime_apply.BundleApplier`.
- Session corruption or broken staged state now surfaces a recovery note instead of silently failing open.
- The CLI loop now forces a grounded final answer after errorful or mixed tool chains so the model cannot cleanly narrate success after tool failures.

## Operator Notes

- `python agent/start_agent.py --clinic-id <id>` requires a reachable OpenAI-compatible model endpoint.
- The CLI uses `--base-url` or `CLINICDX_AGENT_BASE_URL`; if neither is set it defaults to the local Gemma model gateway at `http://127.0.0.1:8085/v1`.
- The control panel calls the same runtime through `/api/agent/message`; it should return plain-language replies, tool summaries, staged-change summaries, validation issues, and next recommended actions.
- Start and verify Gemma 4 E4B with `model_gateway/start-gemma4-e4b-vllm.sh` and `model_gateway/check-model-endpoint.sh`.
- If the model endpoint is unavailable, the session still saves, but the CLI will report a connection error and cannot complete conversational validation.

## Validation Snapshot

Validation performed on this repository state:

- Automated regression:
  - `python -m unittest discover -s tests -p "test_agent_layer.py" -v`
  - Result: `103` tests passed
- Live tool-chain validation against the real control-plane data on this machine:
  - `get_current_config` returned `NO_ACTIVE_CONFIG`, which matches the current store state under `data/emr-os-control-plane/`
  - `compose_from_packs({"hospital_pack": "general-outpatient"})` created a working clinic
  - `get_clinic_inventory(state="working")` returned grounded counts for locations, departments, forms, and queues
  - `get_room_forms(location_name="Consultation Room", state="working")` mapped the room to `consultation-form`
  - `add_form_section` and `add_form_question` staged a TB screening section/question in the working copy
  - `preview_change` succeeded and recorded a pending bundle
  - `apply_change` without confirmation was blocked as expected
  - `apply_change({"confirmed": true, "dry_run": true})` succeeded
- Conversational CLI validation:
  - Attempted with `python agent/start_agent.py --clinic-id agent-e2e-readonly`
  - Blocked by external dependency: the configured/default model endpoint returned a connection error, so a true NL end-to-end run could not be completed in this environment
