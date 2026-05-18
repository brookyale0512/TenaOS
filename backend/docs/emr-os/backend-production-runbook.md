# Backend Production Runbook

This runbook describes the production-oriented operator flow for the backend control plane.

## 1. Preflight

- Confirm Docker and Docker Compose are available on the host.
- Confirm `.env` is present and `.secrets.env` exists or can be generated.
- Confirm the CIEL SQLite store is available, or build it first:

```bash
python -m lmic_emr_os.cli build-ciel-store --repo-root .
```

## 2. Validate Golden Fixtures

Before promoting backend changes, confirm the golden clinic fixtures still pass strict concept validation:

```bash
python -m lmic_emr_os.cli validate-config \
  examples/emr-os/archetypes/general-outpatient-clinic.json \
  --repo-root . \
  --validate-concepts

python -m lmic_emr_os.cli validate-config \
  examples/emr-os/archetypes/specialty-clinic.json \
  --repo-root . \
  --validate-concepts

python -m lmic_emr_os.cli validate-config \
  examples/emr-os/archetypes/hospital-composition.json \
  --repo-root . \
  --validate-concepts
```

## 3. Build And Review A Change Bundle

```bash
python -m lmic_emr_os.cli build-change-bundle \
  examples/emr-os/archetypes/general-outpatient-clinic.json \
  /tmp/change-bundles
```

Review these bundle artifacts before approval:

- `preview.json`
- `operational-analysis.json`
- `verification-plan.json`
- `artifact-manifest.json`
- `openmrs/manifest.json`

## 4. Approve, Apply, Verify, Roll Back

Environment-aware approvals are supported directly from the CLI:

```bash
python -m lmic_emr_os.cli approve-change \
  /tmp/change-bundles/<change-id> \
  --approver ops.lead \
  --environment staging \
  --note "approved for staging"
```

Apply with verification:

```bash
python -m lmic_emr_os.cli apply-change \
  /tmp/change-bundles/<change-id> \
  --environment staging \
  --restart-services \
  --run-verify
```

Notes:

- OpenMRS metadata bundles now force the required OpenMRS restart/load path even when `--restart-services` is omitted, so Initializer-backed changes do not remain merely staged on disk.
- Keep `--restart-services` when the selected products include OpenELIS or Orthanc and you want those services restarted as part of the run.
- OpenMRS queue extension writes now retry transient `QueueEntry.service.null` failures after restart, which covers the short window where the Queue module is up before the freshly loaded queue service metadata is fully usable.
- Shared queue-local concepts now keep stable UUIDs across the golden fixtures for repeated queue services, queue status/priority concepts, and the managed queue service set, which avoids cross-fixture UUID drift when multiple bundles are applied against one runtime.
- OpenELIS apply now waits for the HTTPS login surface and the FHIR metadata endpoint to come back before `--run-verify` hands off to the generic smoke script.
- Keycloak plan apply now resolves runtime `${...}` placeholders and can create missing product client claim roles referenced by managed realm-role composites before reconciling realm roles and users.
- Orthanc policies are now emitted for the Keycloak realm role names that appear in tokens, including aliases introduced through `keycloakRoles`, so specialty bundles can reuse baseline realm-role names without breaking imaging access.
- `--run-verify` now runs `scripts/verify-backend.sh --healthcheck` first and then executes the bundle-specific verification plan. Verification exceptions are recorded as failed runs and still trigger automatic rollback instead of aborting the process without cleanup.

Promote the approved bundle metadata after a successful environment run:

```bash
python -m lmic_emr_os.cli promote-change \
  /tmp/change-bundles/<change-id> \
  --from-environment staging \
  --to-environment prod \
  --promoted-by ops.lead \
  --note "passed staging validation"
```

Roll back with verification if required:

```bash
python -m lmic_emr_os.cli rollback-change \
  /tmp/change-bundles/<change-id> \
  --environment staging \
  --restart-services \
  --run-verify
```

If you pass `--run-id`, it must point to a successful `apply` run. Dry-runs, prior rollback runs, and failed apply runs are rejected before restore begins.

## 5. Run Golden Live Gates

Use the new live-gate runner to exercise the outpatient, specialty, and hospital golden fixtures against a running stack:

```bash
bash ./scripts/run-live-gates.sh \
  --approver ops.live-gate \
  --output-dir /tmp/clinicdx-live-gates
```

Useful variations:

```bash
# Reuse an already running stack
bash ./scripts/run-live-gates.sh --skip-start

# Keep the stack running for inspection after the gates complete
bash ./scripts/run-live-gates.sh --keep-running

# Limit the gates to selected products
bash ./scripts/run-live-gates.sh --product openmrs --product keycloak
```

The gate summary is written to `live-gate-summary.json` under the chosen output directory.

## 6. Production Image Promotion

The release path is now gated in two layers:

- `build-image.yml` runs the Python unit and control-plane regression suite before publishing a build image.
- `scan-sign-promote.yml` runs vulnerability scanning and `scripts/run-live-gates.sh` against the exact candidate digest before signing and promoting it.

For production deployments, use the promoted signed digest:

```bash
CLINICDX_IMAGE_REFERENCE=ghcr.io/clinicdx/clinicdx-backend@sha256:<approved-release-digest>
```
