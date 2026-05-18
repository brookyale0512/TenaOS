# Change Control And Apply Workflow

The backend control plane now supports a bounded change-management workflow on top of generated change bundles.

## Implemented Components

- `lmic_emr_os/change_control.py`
  - Persistent state store for registered changes, approvals, apply runs, rollback runs, and active-change tracking.
- `lmic_emr_os/runtime_apply.py`
  - Runtime applier for OpenMRS, OpenELIS, Orthanc, and Keycloak.
- `lmic_emr_os/cli.py`
  - CLI commands for approval, dry-run, apply, list, and rollback.

## State Model

By default, control-plane state is written under:

```text
data/emr-os-control-plane/
```

This state directory stores:

- registered change records
- approval entries
- apply/rollback run history
- per-run snapshots for file-based rollback
- the currently active change id

## CLI Workflow

### 1. Build A Change Bundle

```bash
python -m lmic_emr_os.cli build-change-bundle \
  examples/emr-os/archetypes/general-outpatient-clinic.json \
  /tmp/change-bundles
```

### 2. Approve The Change

```bash
python -m lmic_emr_os.cli approve-change \
  /tmp/change-bundles/<change-id> \
  --approver ops.lead \
  --note "reviewed and approved"
```

### 3. Dry-Run The Apply

```bash
python -m lmic_emr_os.cli apply-change \
  /tmp/change-bundles/<change-id> \
  --dry-run
```

### 4. Apply To Runtime Surfaces

```bash
python -m lmic_emr_os.cli apply-change \
  /tmp/change-bundles/<change-id> \
  --restart-services \
  --run-verify
```

### 5. Roll Back The Last Successful Apply

```bash
python -m lmic_emr_os.cli rollback-change \
  /tmp/change-bundles/<change-id> \
  --restart-services \
  --run-verify
```

### 6. Inspect Tracked Changes

```bash
python -m lmic_emr_os.cli list-changes
```

## Bundle Verification Artifact

Each bundle now also includes `verification-plan.json`, which lists bundle-specific post-apply checks for Keycloak, OpenMRS, OpenELIS, Orthanc, and workflow integrity.

## Product Apply Semantics

### OpenMRS

- snapshots `/opt/openmrs/data/configuration`
- replaces the direct Initializer domain contents from the generated pack
- always restarts the `openmrs` supervisor program after apply/rollback so Initializer-backed metadata becomes live before the control plane reports success

### OpenELIS

- snapshots and writes `/run/secrets/extra.properties`
- snapshots and writes `/var/lib/openelis-global/properties/common.properties`
- optional targeted restart of `openelis-webapp` and `openelis-fhir`

### Orthanc

- snapshots and writes `/opt/clinicDx/configs/orthanc-auth/permissions.json`
- snapshots and patches `/opt/clinicDx/configs/orthanc/orthanc.json` authorization overlay
- optional targeted restart of `orthanc-auth` and `orthanc`

### Keycloak

- uses documented admin REST endpoints for:
  - create realm role if missing
  - create user if missing
  - update existing user details
  - add missing realm-role mappings
- snapshots relevant roles/users before apply
- rollback deletes users and roles created by the change and restores managed realm-role mappings for pre-existing users

## Current Safety Boundaries

- approval is enforced when the clinic bundle says `approvalRequired=true`
- dry-run never touches runtime surfaces
- apply and rollback are re-runnable from the bundle source of truth
- rollback is strongest for file-based products and additive-safe for identity
- live apply still assumes a privileged operator context on the host

## Current Limitations

- this is a privileged backend tool, not yet an always-on service
- OpenELIS auth property changes are still treated as stack-level configuration rather than clinic-level deltas
- Orthanc runtime config changes are currently re-runnable but not yet persisted through a dedicated control-plane volume
- OpenMRS queue, queue-room, queue-room-provider, service-level pricing, and stock-rule changes are now applied live through bounded handlers, but routing policy still remains adapter-owned because the Queue module lacks a native durable route-rule surface
