# Roadmap Audit

This document audits the current implementation against the original LMIC EMR OS roadmap.

## Status Legend

- `completed`: implemented in the repository and verified with code/tests/docs
- `partial`: meaningful implementation exists, but important roadmap outputs or execution paths are still missing
- `not started`: no substantive implementation yet

## Phase 0

### Ground-truth audit and proof of supported surfaces

Status: `partial`

Completed:

- runtime capability matrix generated from repository/runtime artifacts in `docs/emr-os/runtime-capability-matrix.md`
- verified OpenMRS module inventory generated from runtime cache artifacts
- supported surface catalog effectively captured in the capability matrix and control-plane docs
- first-pass clinic configuration model implemented in `lmic_emr_os/config_model.py`

Missing or partial:

- no separate risk register artifact existed originally
- module inventory is grounded in shipped runtime artifacts, but not yet verified by direct introspection of a running image for every targeted module surface
- no automated runtime probe yet enumerates product REST/docs endpoints beyond the existing smoke checks

## Phase 1

### Metadata-first configuration substrate

Status: `partial`

Completed:

- versioned clinic configuration schema implemented in `lmic_emr_os/config_model.py`
- validation pipeline implemented in `lmic_emr_os/validation.py`
- OpenMRS configuration pack generator implemented in `lmic_emr_os/openmrs_pack.py`
- CIEL-backed terminology validation implemented in `lmic_emr_os/ciel.py`
- durable control-plane state store implemented in `lmic_emr_os/change_control.py`

Partial or missing:

- Initializer generation currently covers locations, local concepts/concept sets, encounter types, forms, idgen, programs/workflows, and billing CSV domains
- queue/routing, pricing, stock/pharmacy, and identity still use bounded extension manifests or external plans rather than first-class OpenMRS metadata-pack domains, even though the runtime now executes queues, queue rooms/providers, service-level prices, and stock rules through audited handlers
- the durable configuration repository exists as bundle/state output, but there is not yet a dedicated versioned config repository service outside the source tree

## Phase 2

### Outpatient clinic onboarding in under one hour

Status: `partial`

Completed:

- first onboarding/control-plane flow implemented in `lmic_emr_os/onboarding.py`
- general outpatient archetype implemented in `examples/emr-os/archetypes/general-outpatient-clinic.json`
- specialty and hospital archetypes also added
- bounded identity flows implemented through Keycloak apply logic in `lmic_emr_os/runtime_apply.py`

Partial or missing:

- onboarding is backend-driven and structured, but not yet a true conversational agent runtime with session memory and NL-to-config transformation
- live-gate rehearsal now exercises build/apply/verify/rollback for the golden fixtures, but no end-to-end patient transaction script yet proves a live `registration -> triage -> consult -> cashier -> lab/pharmacy` workflow
- the "under one hour" target is not yet benchmarked

## Phase 3

### Operational workflows, routing, billing, and pharmacy hardening

Status: `partial`

Completed:

- operational-analysis engine implemented in `lmic_emr_os/operational_analysis.py`
- route simulation, workflow graph output, and KPI-ready metric definitions now generated into bundles
- billing pricing rules and stock/pharmacy rules exist in the config model and extension manifests
- supported OpenMRS execution surfaces for queue/routing, billing pricing, and stock/pharmacy have now been audited in `docs/emr-os/openmrs-extension-surface-audit.md`
- bounded OpenMRS live handlers now apply queue, queue-room, queue-room-provider, service-level billing prices, and stock rules through supported REST surfaces
- OpenMRS apply/rollback now forces restart so Initializer-backed metadata becomes live instead of only being staged on disk

Partial or missing:

- routing policy remains adapter-owned policy in the live OpenMRS extension layer because the Queue module has no native persistence surface for durable route rules
- some billing and stock semantics still remain policy-layer only, especially patient-category/payment-gating semantics and stock operation-type/location behavior outside the audited mutable REST resources
- no real bottleneck analytics engine exists yet beyond metric definitions and route simulation

## Phase 4

### Lab and imaging control plane integration

Status: `partial`

Completed:

- OpenELIS adapter implemented in `lmic_emr_os/adapters.py`
- Orthanc adapter implemented in `lmic_emr_os/adapters.py`
- workflow/access profiles added for OpenELIS and Orthanc
- cross-product verification plan generated in `lmic_emr_os/verification_plan.py`

Partial or missing:

- verification suite now executes `scripts/verify-backend.sh` plus bundle-specific verification-plan checks during apply, and rollback runs now execute bundle-aware rollback verification, but some endpoint/auth checks are still delegated to the smoke script
- OpenELIS and Orthanc are still mostly property/policy-driven adapters and not full-featured product-specific configuration engines

## Phase 5

### Hospital composition and specialty expansion

Status: `partial`

Completed:

- one hospital composition archetype exists in `examples/emr-os/archetypes/hospital-composition.json`
- one specialty clinic archetype exists in `examples/emr-os/archetypes/specialty-clinic.json`
- reusable department-pack library implemented in `examples/emr-os/department-packs/`
- composition plans implemented in `examples/emr-os/compositions/`
- hospital composition engine implemented in `lmic_emr_os/department_composition.py`

Partial or missing:

- no multi-department orchestration rules engine exists yet beyond general route analysis on a finished config
- no shared-capacity/conflict-resolution layer yet exists when multiple packs compete for the same operational resources

## Phase 6

### Frontend independence and operator UX

Status: `not started`

Completed:

- backend APIs/artifacts are frontend-independent by design
- Mermaid graph output and structured bundle artifacts provide a backend substrate for future UX

Missing:

- no decoupled admin frontend
- no import/export UX
- no visual facility map or operator dashboard beyond generated files

## Validation And Safety Audit

Status: `partial`

Implemented:

- concept existence and retired-concept blocking via `lmic_emr_os/ciel.py`
- queue/billing/user-role structural validation in `lmic_emr_os/validation.py`
- preview, assumptions, workflow graph, approval gating, audit trail, rollback metadata, and bounded apply/rollback flow
- post-apply smoke-test hook wired to `scripts/verify-backend.sh`
- `verification-plan.json` is now executed during `apply-change --run-verify`
- OpenMRS runtime verification now checks that restarted metadata is visible through supported runtime surfaces after apply
- queue status/priority concept-set presence is now validated live through queue runtime verification after apply

Missing or partial:

- some verification checks are still delegated to `scripts/verify-backend.sh` instead of being executed independently per plan item
- rollback verification is now bundle-aware, but it still depends partly on the shared smoke script instead of a fully standalone product-by-product restore validator
- routing policy verification still proves adapter-owned policy installation rather than native Queue-module persistence, because that persistence surface does not exist

## Workstream Audit

### Completed workstreams

- validate runtime surfaces and produce capability matrix
- design universal clinic configuration model
- implement OpenMRS metadata compiler
- wrap CIEL as a deterministic terminology service
- build onboarding/change-bundle engine
- add OpenELIS and Orthanc adapters
- add archetypes and hospital example
- add bounded change-control/apply/rollback layer
- add operational-analysis and verification-plan generation

### Most important remaining gaps

1. Replace adapter-owned OpenMRS routing policy with a stronger native or explicitly hosted runtime contract, because Queue still lacks durable route-rule persistence.
2. Deepen runtime verification so fewer checks depend on `scripts/verify-backend.sh` and more rollback semantics are proven by product-specific checks alone.
3. Add richer multi-department orchestration/conflict resolution on top of the existing department-pack and composition engine.
4. Add true conversational session orchestration and NL-to-config progression on top of the backend control plane.
5. Add a dedicated config repository/service boundary outside the source tree.

## Current Overall Assessment

The roadmap is no longer conceptual only. The repository now contains a real backend control-plane foundation that covers most of Phases 0 through 4 in a partial but meaningful way. The biggest remaining gap is no longer "there is no runtime apply story"; that part is real. The narrower remaining gaps are route-policy durability, deeper product-specific verification, richer multi-department orchestration, a standalone config-repository boundary, and the still-deferred conversational orchestration layer.
