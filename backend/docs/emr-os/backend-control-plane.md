# Backend Control Plane

The implementation added in this repository is a backend-first control plane for an LMIC-focused conversational setup product. It does not let the runtime agent edit source code or write directly to product databases.

## Core Components

- `lmic_emr_os/runtime_inventory.py`
  - Scans the repo/runtime artifacts and emits the capability matrix in `docs/emr-os/`.
- `lmic_emr_os/config_model.py`
  - Defines the universal clinic configuration model used by the agent and validators.
- `lmic_emr_os/validation.py`
  - Performs reference validation, structural checks, and optional concept validation through CIEL.
- `lmic_emr_os/ciel.py`
  - Wraps the local `CIEL` toolkit, builds the SQLite store on demand, and optionally enables Qdrant hybrid retrieval.
- `lmic_emr_os/openmrs_pack.py`
  - Compiles a clinic config into an OpenMRS pack plus bounded extension manifests.
- `lmic_emr_os/runtime_apply.py`
  - Applies, verifies, and rolls back bundles against bounded runtime surfaces, expands runtime placeholders in generated plans, and exposes the current-config read path used by higher-level automation.
- `lmic_emr_os/adapters.py`
  - Generates OpenELIS, Orthanc, and Keycloak runtime plans from the same clinic model, including Keycloak realm-role composites that can reference product client claim roles.
- `lmic_emr_os/onboarding.py`
  - Provides interview sections, previews, change bundles, apply order, and rollback metadata.
- `lmic_emr_os/cli.py`
  - Command-line entrypoint to run the control-plane workflows.

## Supported Workflows

### Inventory Runtime Surfaces

```bash
python -m lmic_emr_os.cli inventory --repo-root . --output-dir docs/emr-os
```

### Validate A Clinic Bundle

```bash
python -m lmic_emr_os.cli validate-config examples/emr-os/archetypes/general-outpatient-clinic.json
```

### Validate A Clinic Bundle With CIEL Checks

```bash
python -m lmic_emr_os.cli validate-config examples/emr-os/archetypes/general-outpatient-clinic.json --validate-concepts
```

### Compile An OpenMRS Pack

```bash
python -m lmic_emr_os.cli compile-openmrs \
  examples/emr-os/archetypes/general-outpatient-clinic.json \
  /tmp/sunrise-openmrs-pack
```

### Build A Full Change Bundle

```bash
python -m lmic_emr_os.cli build-change-bundle \
  examples/emr-os/archetypes/hospital-composition.json \
  /tmp/change-bundles
```

### Analyze Operational Flow

```bash
python -m lmic_emr_os.cli analyze-operations \
  examples/emr-os/archetypes/hospital-composition.json
```

### Generate A Verification Plan

```bash
python -m lmic_emr_os.cli verification-plan \
  examples/emr-os/archetypes/hospital-composition.json
```

### List Reusable Department Packs

```bash
python -m lmic_emr_os.cli list-department-packs
```

### Compose A Hospital From Department Packs

```bash
python -m lmic_emr_os.cli compose-hospital \
  examples/emr-os/compositions/district-hospital-plan.json \
  /tmp/composed-district-hospital.json
```

### Approve And Dry-Run A Change

```bash
python -m lmic_emr_os.cli approve-change /tmp/change-bundles/<change-id> --approver ops.lead --environment staging
python -m lmic_emr_os.cli apply-change /tmp/change-bundles/<change-id> --dry-run
```

### Apply And Roll Back A Change

```bash
python -m lmic_emr_os.cli apply-change /tmp/change-bundles/<change-id> --restart-services --run-verify
python -m lmic_emr_os.cli rollback-change /tmp/change-bundles/<change-id> --restart-services --run-verify
```

### Promote A Change Between Environments

```bash
python -m lmic_emr_os.cli promote-change \
  /tmp/change-bundles/<change-id> \
  --from-environment staging \
  --to-environment prod \
  --promoted-by ops.lead
```

### Run Golden Live Gates

```bash
python -m lmic_emr_os.cli run-live-gates --repo-root . --output-dir data/emr-os-live-gates
```

### Build The Local CIEL Store

```bash
python -m lmic_emr_os.cli build-ciel-store --repo-root .
```

## Agent-Facing APIs

These APIs are not exposed as standalone CLI commands, but they are the intended stable surface for a future orchestration service:

- `lmic_emr_os.validation.load_and_validate(payload, concept_resolver=...)`
  - Runs schema loading plus semantic validation and returns one structured result object instead of mixing schema exceptions with validation reports.
- `lmic_emr_os.runtime_apply.BundleApplier.get_current_config(products=None, require_consistent=True)`
  - Reads the currently active clinic configuration from control-plane state so an automation layer can propose deltas against live state instead of reconstructing it ad hoc.
- `lmic_emr_os.ciel.CielTerminologyService.search_concepts(...)`
  - Returns stable JSON-serializable search hits instead of leaking internal `ciel_search` objects across the control-plane boundary.

## Agent Runtime Contract

The interactive runtime contract for `agent/start_agent.py` and `agent/emr_mcp_server.py` is documented in `docs/emr-os/agent-operator-contract.md`.

At a high level, the hardened runtime now:

- separates `active` live config from the session-local `working` copy and `pending` preview bundle
- persists working state and preview state per `clinic_id` under `data/sessions/`
- exposes read-only inventory/query tools so read questions do not mutate config state
- requires explicit user confirmation between `preview_change` and `apply_change`
- keeps form-section and form-question add operations idempotent by ID

## Change Bundle Contents

Each onboarding/change bundle includes:

- `artifact-manifest.json`
- `clinic-config.json`
- `preview.json`
- `operational-analysis.json`
- `workflow-graph.mmd`
- `verification-plan.json`
- `openmrs/`
- `plans/openelis-plan.json`
- `plans/orthanc-plan.json`
- `plans/keycloak-plan.json`
- `apply-order.json`
- `rollback.json`
- tracked control-plane state under `data/emr-os-control-plane/` once the bundle is registered/applied

During Keycloak apply, the runtime now expands `${...}` placeholders inside plan payloads and creates any missing client claim roles referenced by managed realm-role composites before reconciling users.

During OpenELIS apply with service restarts enabled, the runtime waits for both the public web endpoint and the FHIR metadata endpoint to return healthy responses before post-apply verification continues.

During OpenMRS extension apply, queue upserts now retry transient Queue-module `service null` validation errors until the freshly loaded metadata bundle becomes usable through the queue REST surface.

Shared queue-local concepts now keep stable UUIDs across archetypes for repeated operational concepts such as registration service, cashier service, queue status members, queue priority members, and the managed queue service set, which prevents later bundles from pointing OpenMRS at queue concept UUIDs that were never loaded in the runtime.

Orthanc plans now emit permissions and access profiles for the actual Keycloak realm role names carried in tokens, including aliases created through `keycloakRoles`, so specialty clinic roles can reuse baseline realm-role names without losing Orthanc access.

When `--run-verify` is enabled, the runtime now executes `scripts/verify-backend.sh --healthcheck` as the generic smoke gate and then runs the bundle-specific verification plan. Verification exceptions are captured as normal apply or rollback failures so the control plane can still record the run and trigger automatic rollback instead of crashing mid-run.

## Current Boundary

This implementation is now a safe backend foundation plus a bounded privileged apply path. It can approve, promote, dry-run, apply, verify, live-gate, and roll back generated change bundles through supported runtime surfaces, but it is still operator-driven and intentionally conservative about what it mutates live.
