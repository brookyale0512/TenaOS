# ClinicConfigModel

`ClinicConfigModel` is the backend contract for the conversational setup agent. The runtime agent should produce and revise this model, and every downstream step should validate or compile from it rather than editing product internals.

## Source Of Truth

- Python model: `lmic_emr_os/config_model.py`
- Validator: `lmic_emr_os/validation.py`
- OpenMRS compiler: `lmic_emr_os/openmrs_pack.py`
- Onboarding/change bundle orchestration: `lmic_emr_os/onboarding.py`

## Top-Level Domains

- `facilityProfile`
  - Facility identity, country, timezone, languages, and default currency.
- `locations`
  - Facility, department, room, and service-area structure used across OpenMRS, queueing, billing, stock, lab, and imaging.
- `staffingModel`
  - Clinic operating roles and which queues they work in.
- `registrationModel`
  - Identifier strategy, login location, walk-in policy, and appointment mode.
- `encounterTypes`
  - Encounter definitions used by forms and clinical workflows.
- `forms`
  - O3-compatible form definitions made of pages, sections, questions, and coded answers.
- `queues`
  - Operational service points tied to locations and concept-backed status/priority sets.
- `queueRooms`
  - Room-level detail for queue services.
- `routingRules`
  - Structured next-step transitions between queue services.
- `billingModel`
  - Billable services, payment modes, cash points, pricing rules, and billing-related global properties.
- `stockPharmacyModel`
  - Stock locations, operation types, reorder rules, and dispensing queue bindings.
- `programs`, `programWorkflows`, `programWorkflowStates`
  - Longitudinal clinical programs separate from same-day patient routing.
- `labModel`
  - OpenELIS integration posture and result-routing assumptions.
- `imagingModel`
  - Orthanc enablement, role permissions, and share-policy defaults.
- `identityModel`
  - Clinic roles plus initial users, mapped to product-native permissions.
- `governance`
  - Approval rules, requestor limits, and dry-run/change-ticket defaults.

## Validation Rules

The validator checks:

- required facility identity and at least one location
- location, queue, room, routing, billing, stock, program, workflow, and role references
- form encounter references and concept requirements for `obs` questions
- governance requestor roles
- optional concept existence and retired-concept blocking through `CielTerminologyService`

## Key Modeling Rule

- clinical meaning belongs in CIEL-backed concept references
- operational structure belongs in local clinic metadata
- same-day movement belongs in queues and routing rules
- longitudinal disease/program state belongs in program workflows

## Current Compilation Boundary

The current implementation compiles direct OpenMRS Initializer domains for:

- `locations`
- `encountertypes`
- `globalproperties`
- `ampathforms`
- `idgen`
- `programs`
- `programworkflows`
- `programworkflowstates`
- `billableservices`
- `paymentmodes`
- `cashpoints`

The current implementation emits bounded extension manifests for:

- queue/routing configuration
- pricing rules
- stock/pharmacy operational structures
- identity provisioning plans

Those extension manifests are intentional. They separate direct metadata-pack loading from domains that need API/admin-surface application.
