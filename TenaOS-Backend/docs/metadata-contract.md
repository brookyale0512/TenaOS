# TenaOS OpenMRS Metadata Contract

Phase 1 relies on OpenMRS Reference Application 3 plus any metadata staged in `metadata/openmrs-managed-config`.

## Required REST Primitives

| Primitive | Endpoint | Frontend dependency |
| --- | --- | --- |
| Session | `/openmrs/ws/rest/v1/session` | Confirms OpenMRS native REST is available. |
| Location | `/openmrs/ws/rest/v1/location` | Registration, visits, vitals, notes, form encounters. |
| Patient identifier type | `/openmrs/ws/rest/v1/patientidentifiertype` | Patient registration. |
| Visit type | `/openmrs/ws/rest/v1/visittype` | Start visit dialog. |
| Form | `/openmrs/ws/rest/v1/form` and `/openmrs/ws/rest/v1/o3/forms/{uuid}` | Form catalog and encounter capture. |
| Queue | `/openmrs/ws/rest/v1/queue` and `/openmrs/ws/rest/v1/queue-entry` | Queue dashboard and queue intake. |

## Clinical UUIDs Currently Used By The Frontend

The first phase keeps the existing UUID references so we can quickly prove OpenMRS connectivity. Before production hardening, replace these with metadata discovery or config-driven values.

- Vitals encounter type: `67a71486-1a54-468f-ac3e-7be3e1b0e6a0`
- Clinical note encounter type: `d7151f82-c1f3-4152-a605-2f9ea7414a79`
- Vitals concepts:
  - Temperature: `5088AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Systolic BP: `5085AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Diastolic BP: `5086AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Pulse: `5087AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Oxygen saturation: `5092AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Respiratory rate: `5242AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Height: `5090AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`
  - Weight: `5089AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA`

## Deferred Metadata Pickers

The UI explicitly defers workflows that need stronger metadata pickers:

- Allergy creation: coded allergen, severity, and reaction concepts.
- Medication ordering: drug, dose unit, route, frequency, and duration concepts.
- Note creation: requires `VITE_CLINICAL_NOTE_CONCEPT_UUID` until a text-note concept is selected in metadata.

## Verification

`./scripts/verify-lite.sh` validates the generic primitives. The end-to-end workflow validates queue, encounter, and form behavior against the actual OpenMRS data returned by the lite stack.
