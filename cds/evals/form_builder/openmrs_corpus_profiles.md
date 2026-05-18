# Real OpenMRS Form Corpus Profiles

This file profiles the first real-world form corpus cloned under `/var/www/forms` for ClinicDx Lite form-builder benchmarking.

## AMPATH Demo Triage

- Source: `/var/www/forms/AMPATH-openmrs-angularFormentry/app/scripts/formentry/schema/demo-triage.json`
- Form name: `triage`
- Purpose: concise triage vital-signs form.
- Structure: one page with `Encounter Details` and `Vital Signs`.
- Benchmarkable concepts:
  - `5085` systolic blood pressure
  - `5086` diastolic blood pressure
  - `5087` pulse rate
  - `5088` temperature
  - `5089` weight
  - `5090` height
  - `5092` oxygen saturation
- Current-builder fit: strong. The local builder already supports numeric obs, section grouping, deterministic rendering, and CIEL padded UUID publication.
- Gaps to watch: source form has numeric min/max constraints; current basket operations do not capture min/max validator metadata.

## OpenMRS TB Case Enrollment

- Source: `/var/www/forms/openmrs-openmrs-form-engine-lib/__mocks__/forms/rfe-forms/post-submission-test-form.json`
- Form name: `TB Case Enrollment Form`
- Purpose: enroll a client into a drug-susceptible or drug-resistant TB program.
- Structure: one page, one `TB Program` section.
- Benchmarkable concepts:
  - `163775` TB program type
  - `161552` date enrolled in tuberculosis care
  - `161654` DS TB treatment number
- Current-builder fit: moderate. The core obs fields can be generated.
- Gaps to watch: source form has `postSubmissionActions` for program enrollment, enabled expressions, and program UUID wiring. The current form builder only creates encounter obs forms and does not model program-enrollment side effects.

## OpenMRS HIV Service Enrolment

- Source: `/var/www/forms/openmrs-openmrs-form-engine-lib/__mocks__/forms/rfe-forms/test-enrolment-form.json`
- Form name: `Service Enrolment Form`
- Purpose: enroll or re-enroll a client for HIV care.
- Structure: introduction markdown, `Client Profile`, and `Notes`.
- Benchmarkable concepts:
  - `160555` enrollment date
  - `162576` unique ID
  - `166432` population category
  - `165095` general notes
- Current-builder fit: moderate. Date, text, coded/radio, and notes-style fields are within or near current rendering behavior.
- Gaps to watch: source form includes markdown-only content, behavior metadata, transient fields, validation expressions, and one non-CIEL UUID concept for patient type at enrollment.

## AMPATH Adult Return Visit

- Source: `/var/www/forms/AMPATH-ngx-openmrs-formentry/src/app/adult.json`
- Form name: `AMPATH POC Adult Return Visit Form v1.4`
- Purpose: large outpatient adult return visit workflow.
- Structure: 46 sections and 126 obs questions with many reusable form references.
- Current-builder fit: qualitative only for now. The form is clinically valuable, but most concept references are AMPATH/OpenMRS UUIDs rather than padded numeric CIEL IDs.
- Gaps to watch:
  - referenced subforms
  - person attributes and encounter metadata controls
  - conditional required fields
  - hide/disable expressions
  - historical expressions
  - rich coded answer sets with local UUID concepts
  - validation rules beyond datatype-level validation

## Capability Gaps Observed From The Corpus

- The current builder does not preserve numeric min/max constraints from source forms.
- The current builder does not model O3 `hide`, `disable`, `behaviours`, `historicalExpression`, `calculate`, or JS expression validators.
- The current builder does not generate `postSubmissionActions` such as program enrollment.
- The current builder does not import or resolve non-padded AMPATH/OpenMRS concept UUIDs back to CIEL numeric IDs.
- The current builder intentionally emits deterministic O3 schema from a CIEL concept basket; this is safer than raw LLM JSON but limits parity with complex production forms.
