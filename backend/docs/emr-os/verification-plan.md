# Verification Plan

Each generated clinic bundle now includes a structured `verification-plan.json` file. This turns post-apply verification into an explicit artifact of the bundle instead of leaving it to ad hoc operator memory.

## Implementation

- generator: `lmic_emr_os/verification_plan.py`
- CLI: `python -m lmic_emr_os.cli verification-plan <config>`
- bundle integration: `lmic_emr_os/onboarding.py`

## What It Covers

The verification plan currently includes checks for:

- Keycloak discovery
- OpenMRS REST and FHIR endpoints
- OpenMRS staged metadata/config presence
- OpenMRS runtime bundle-load verification after restart
- Keycloak realm roles and provisioned users from the clinic bundle
- OpenELIS login/FHIR/property checks when lab is enabled
- Orthanc policy and DICOMweb checks when imaging is enabled
- operational workflow analysis status
- billing metadata seeding expectations
- stock/pharmacy operational manifest presence

## Why It Exists

This file is meant to bridge generated configuration and live verification:

- `preview.json` answers "does the bundle look plausible?"
- `operational-analysis.json` answers "does the patient flow make sense?"
- `verification-plan.json` answers "what exactly should we verify after apply?"

## CLI Example

```bash
python -m lmic_emr_os.cli verification-plan \
  examples/emr-os/archetypes/hospital-composition.json
```

## Relationship To `verify-backend.sh`

The repo already has `scripts/verify-backend.sh`. The new verification plan does not replace it. Instead:

- `verify-backend.sh` remains the generic backend smoke test
- `verification-plan.json` adds bundle-specific expectations and product-specific checks derived from clinic configuration
- `python -m lmic_emr_os.cli apply-change ... --run-verify` now runs `scripts/verify-backend.sh --healthcheck` first and then executes the bundle-specific verification plan checks

## Current Limitation

The verification plan is no longer purely declarative: the apply layer now executes it during `--run-verify`, including OpenMRS runtime bundle checks after restart. The remaining limitations are narrower:

- some HTTP/auth checks are still delegated to `scripts/verify-backend.sh`
- rollback verification is still generic rather than validating the exact restored prior bundle
- adapter-owned routing policy can be verified as installed, but not as native Queue-module persistence because that persistence surface does not exist
